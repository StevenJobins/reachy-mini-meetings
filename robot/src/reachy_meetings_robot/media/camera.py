"""Camera capture with pose-stamped frames.

For latency-hiding reprojection the headset needs to know WHERE the robot head was pointing
when each frame was captured. So every frame is published together with the commanded head
pose at capture time (from the StateStore).

Transport of the video itself is still open (see README "Open questions"):
  a) Daemon WebRTC stream (media_backend="webrtc") + pose metadata over our WebSocket
  b) Our own encoder (e.g. JPEG/H.264 over WebSocket) – simpler to sync, more work
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ..config import CameraCfg
from ..state import RobotState, StateStore

log = logging.getLogger(__name__)


@dataclass
class StampedFrame:
    image: np.ndarray        # (H, W, 3) uint8
    t_capture: float         # time.time() at capture
    pose: RobotState         # commanded head/body pose at capture


FrameCallback = Callable[[StampedFrame], None]


class CameraStreamer:
    def __init__(self, mini, cfg: CameraCfg, state: StateStore) -> None:
        self.mini = mini
        self.cfg = cfg
        self.state = state
        self._subscribers: list[FrameCallback] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.latest: StampedFrame | None = None

    def subscribe(self, cb: FrameCallback) -> None:
        self._subscribers.append(cb)

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        period = 1.0 / self.cfg.rate_hz
        while not self._stop.is_set():
            t0 = time.time()
            pose = self.state.get()  # sample pose as close to capture as possible
            frame = self.mini.media.get_frame()
            if frame is not None:
                sf = StampedFrame(frame, t0, pose)
                self.latest = sf
                for cb in self._subscribers:
                    try:
                        cb(sf)
                    except Exception:
                        log.exception("frame subscriber failed")
            time.sleep(max(0.0, period - (time.time() - t0)))
