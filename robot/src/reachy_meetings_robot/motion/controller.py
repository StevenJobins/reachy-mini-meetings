"""MotionController: the ONE place that sends targets to the robot.

Every tick (mirror.rate_hz):
    head = mirror.step() + gesture offsets + speaking wiggle
    clamp to limits, let the body follow if head yaw gets too far from body yaw
    mini.set_target(head=..., antennas=..., body_yaw=...)
    publish what was commanded to the StateStore (-> bridge -> headset reprojection)

Nothing else should call set_target / goto_target while the controller runs, otherwise the
commands fight each other.
"""

from __future__ import annotations

import logging
import math
import threading
import time

import numpy as np

from ..config import Config
from ..safety import HeadTarget, body_yaw_to_follow, clamp_head
from ..state import StateStore
from .gestures import GesturePlayer
from .head_mirror import HeadMirror

log = logging.getLogger(__name__)


class MotionController:
    def __init__(self, mini, cfg: Config, state: StateStore) -> None:
        self.mini = mini
        self.cfg = cfg
        self.state = state
        self.mirror = HeadMirror(cfg.mirror)
        self.gestures = GesturePlayer(cfg.gestures.default_duration_s)
        self._body_yaw = 0.0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ---- public API (thread-safe enough: single writer per field) ----
    def play_gesture(self, name: str, duration: float | None = None) -> None:
        self.gestures.play(name, duration)

    # ---- loop ----
    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="motion", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        period = 1.0 / self.cfg.mirror.rate_hz
        next_t = time.monotonic()
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("motion tick failed")
            next_t += period
            time.sleep(max(0.0, next_t - time.monotonic()))

    def compose(self) -> tuple[HeadTarget, float, tuple[float, float]]:
        """Pure part of a tick (no robot I/O) – easy to unit-test."""
        base = self.mirror.step()
        r, p, y, al, ar = self.gestures.step()

        if self.state.get().speaking:  # subtle antenna motion while the robot talks
            w = 6 * math.sin(2 * math.pi * 3 * time.monotonic())
            al, ar = al + w, ar - w

        head = HeadTarget(base.roll + r, base.pitch + p, base.yaw + y, base.z_mm)
        lim = self.cfg.limits
        if self.cfg.mirror.body_follow:
            self._body_yaw = body_yaw_to_follow(head.yaw, self._body_yaw, lim)
        head = clamp_head(head, self._body_yaw, lim)
        return head, self._body_yaw, (al, ar)

    def tick(self) -> None:
        from reachy_mini.utils import create_head_pose

        head, body_yaw, (al, ar) = self.compose()
        self.mini.set_target(
            head=create_head_pose(z=head.z_mm, roll=head.roll, pitch=head.pitch, yaw=head.yaw,
                                  mm=True, degrees=True),
            antennas=np.deg2rad([ar, al]),  # SDK order: [right, left]
            body_yaw=float(np.deg2rad(body_yaw)),
        )
        self.state.update(
            head_roll_deg=head.roll, head_pitch_deg=head.pitch, head_yaw_deg=head.yaw,
            head_z_mm=head.z_mm, body_yaw_deg=body_yaw, antennas_deg=(al, ar),
            active_gesture=self.gestures.active, mirror_active=self.mirror.active,
        )
