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

    def _get(self, path: str) -> dict:
        with urllib.request.urlopen(self.url + path, timeout=1) as r:
            return json.loads(r.read())

    def _poll(self, n: int) -> None:
        if n % 5 == 0:  # head pose changes slower than speech, 2 Hz is enough
            yaw = (self._get("/api/state/full").get("head_pose") or {}).get("yaw")
            if yaw is not None:
                self.head_yaw_deg = math.degrees(yaw)
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
