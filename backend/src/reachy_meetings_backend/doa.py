"""Speaker direction for the speech bubbles, polled from the Reachy Mini daemon (HTTP, port 8000).

  GET /api/state/doa   {"angle": rad, "speech_detected": bool}   0 = left, pi/2 = front, pi = right
  GET /api/state/full  {"head_pose": {"yaw": rad, ...}, ...}

Per utterance we send two angles, both with the yaw convention + = left:
  doa_deg      relative to the head = the camera; the headset matches it against the faces in the video
  azimuth_deg  in the robot base frame (head yaw + doa_deg), for a bubble in the room when no face is seen

TODO verify on the real robot: is DoA relative to the head or the body? front/back ambiguity?
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
import urllib.request
from collections import deque

log = logging.getLogger(__name__)

TURNING = 20.0   # degrees a second: above this the head is turning


def doa_to_head_deg(doa_rad: float) -> float:
    return 90.0 - math.degrees(doa_rad)


def circular_mean_deg(angles: list[float]) -> float:
    s = sum(math.sin(math.radians(a)) for a in angles)
    c = sum(math.cos(math.radians(a)) for a in angles)
    return math.degrees(math.atan2(s, c))


class DoaTracker:
    """Keeps the last seconds of (time, doa_deg, head_yaw_deg) while someone speaks."""

    def __init__(self, daemon_url: str, rate_hz: float = 10, keep_s: float = 30.0) -> None:
        self.url = daemon_url.rstrip("/")
        self.period = 1.0 / rate_hz
        self.keep_s = keep_s
        self.samples: deque[tuple[float, float, float]] = deque()
        self.head_yaw_deg = 0.0
        self.head_pitch_deg = self.head_roll_deg = 0.0
        self.poses: deque[tuple[float, float, float, float]] = deque(maxlen=200)   # (time, yaw, pitch, roll)

    def add(self, doa: dict, head_yaw_deg: float, now: float | None = None) -> None:
        # The chip's own speech flag is false most of the time even while someone talks (measured on the page:
        # 8 of 8 readings), so keep every reading: the utterance's time window already says it was speech.
        if not doa or doa.get("angle") is None:
            return
        now = time.time() if now is None else now
        self.samples.append((now, doa_to_head_deg(doa["angle"]), head_yaw_deg))
        while self.samples and self.samples[0][0] < now - self.keep_s:
            self.samples.popleft()

    def direction(self, t0: float, t1: float) -> tuple[float | None, float | None]:
        """(doa_deg, azimuth_deg) averaged over [t0, t1] (wall clock), None if unknown."""
        hits = [(d, y) for t, d, y in self.samples if t0 <= t <= t1]
        if not hits:
            return None, None
        doa = circular_mean_deg([d for d, _ in hits])
        az = circular_mean_deg([d + y for d, y in hits])
        return round(doa, 1), round(az, 1)

    def spread(self, t0: float, t1: float) -> tuple[float, float] | None:
        """(azimuth_deg, circular spread in degrees) over [t0, t1], for fusion.py; None if no readings."""
        az = [d + y for t, d, y in self.samples if t0 <= t <= t1]
        if not az:
            return None
        s = sum(math.sin(math.radians(a)) for a in az) / len(az)
        c = sum(math.cos(math.radians(a)) for a in az) / len(az)
        r = min(1.0, math.hypot(s, c))
        return math.degrees(math.atan2(s, c)), math.degrees(math.sqrt(-2 * math.log(max(r, 1e-9))))

    def set_pose(self, yaw: float, pitch: float, roll: float, now: float | None = None) -> None:
        self.head_yaw_deg, self.head_pitch_deg, self.head_roll_deg = yaw, pitch, roll
        self.poses.append((time.time() if now is None else now, yaw, pitch, roll))

    def pose_at(self, t: float) -> tuple[float, float, float, bool]:
        """(yaw, pitch, roll, steady) of the head at wall clock t: between the two readings around t; steady =
        the head turns slower than TURNING there, so directions worked out with these angles can be trusted (the
        angles are read 10 times a second and the camera picture is a little older than its time says)."""
        if not self.poses:
            return self.head_yaw_deg, self.head_pitch_deg, self.head_roll_deg, True
        poses = list(self.poses)
        after = next((i for i, p in enumerate(poses) if p[0] >= t), len(poses) - 1)
        a, b = poses[max(0, after - 1)], poses[after]
        k = 0.0 if b[0] <= a[0] else min(1.0, max(0.0, (t - a[0]) / (b[0] - a[0])))
        yaw, pitch, roll = (a[i] + k * (b[i] - a[i]) for i in (1, 2, 3))
        rate = 0.0 if b[0] <= a[0] else max(abs(b[1] - a[1]), abs(b[2] - a[2])) / (b[0] - a[0])
        return yaw, pitch, roll, rate < TURNING and t - b[0] < 1.0

    def _get(self, path: str) -> dict:
        with urllib.request.urlopen(self.url + path, timeout=1) as r:
            return json.loads(r.read())

    def _poll(self, n: int) -> None:
        pose = self._get("/api/state/full").get("head_pose") or {}   # people.py turns face directions into room
        if pose.get("yaw") is not None:                              # directions with it
            self.set_pose(math.degrees(pose["yaw"]), math.degrees(pose.get("pitch") or 0.0),
                          math.degrees(pose.get("roll") or 0.0))
        self.add(self._get("/api/state/doa"), self.head_yaw_deg)

    async def run(self) -> None:
        n, ok = 0, None
        while True:
            try:
                await asyncio.to_thread(self._poll, n)
                if ok is not True:
                    log.info("DoA: reading from the daemon at %s", self.url)
                ok = True
            except (OSError, ValueError):
                if ok is not False:
                    log.warning("DoA: daemon not reachable at %s, bubbles without direction", self.url)
                ok = False
                await asyncio.sleep(2.0)
            n += 1
            await asyncio.sleep(self.period)
