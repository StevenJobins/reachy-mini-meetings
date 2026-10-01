"""Headset pose -> robot head (the robot looks where the remote user looks).

Convention: the xr-client sends roll/pitch/yaw in DEGREES already converted to the robot head
frame (see bridge/protocol.py). Conversion from Unity/OpenXR quaternions lives in xr-client.

Pipeline: gain -> deadband -> exponential smoothing -> (stale? decay to neutral).
Clamping + body follow happen in the MotionController.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..config import MirrorCfg
from ..safety import HeadTarget


@dataclass
class HeadsetPose:
    roll: float
    pitch: float
    yaw: float
    t_sent: float  # headset timestamp (s), used for latency measurement


class HeadMirror:
    def __init__(self, cfg: MirrorCfg) -> None:
        self.cfg = cfg
        self._latest: HeadsetPose | None = None
        self._t_received: float = 0.0
        self._out = HeadTarget()

    def push(self, pose: HeadsetPose) -> None:
        """Called by the bridge for every incoming headset pose."""
        self._latest = pose
        self._t_received = time.monotonic()

    @property
    def active(self) -> bool:
        return self._latest is not None and not self._stale()

    def _stale(self) -> bool:
        return time.monotonic() - self._t_received > self.cfg.stale_after_s

    def step(self) -> HeadTarget:
        """One control tick. Returns the smoothed head target (not yet clamped)."""
        if self._latest is None or self._stale():
            goal = HeadTarget()  # drift back to neutral
        else:
            g = self.cfg.gain
            goal = HeadTarget(
                roll=self._latest.roll * g[0],
                pitch=self._latest.pitch * g[1],
                yaw=self._latest.yaw * g[2],
            )

        a = self.cfg.smoothing
        db = self.cfg.deadband_deg

        def filt(prev: float, new: float) -> float:
            if abs(new - prev) < db:
                return prev
            return a * prev + (1 - a) * new

        self._out = HeadTarget(
            roll=filt(self._out.roll, goal.roll),
            pitch=filt(self._out.pitch, goal.pitch),
            yaw=filt(self._out.yaw, goal.yaw),
            z_mm=0.0,
        )
        return self._out

    # TODO: predictive filtering (constant-velocity extrapolation over measured latency)
    #       so the robot's real head pose lags the user less. Measure latency first.
