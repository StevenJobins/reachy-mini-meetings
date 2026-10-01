"""Speaker direction for the speech bubbles, from the robot bridge's `state` messages.

The robot (with media enabled) publishes `speaker_doa_rad` + `speech_detected` from the
mic array, plus the commanded head yaw. DoA convention (SDK): 0 = left, pi/2 = front, pi = right,
relative to the head. We turn it into an azimuth in the robot's base frame, yaw convention
(+ = left), so the headset can place the bubble the same way it places the video window.

TODO verify on the real robot: is DoA relative to the head or the body? front/back ambiguity?
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import deque

import websockets

log = logging.getLogger(__name__)


def doa_to_azimuth_deg(doa_rad: float, head_yaw_deg: float) -> float:
    return head_yaw_deg + 90.0 - math.degrees(doa_rad)


def circular_mean_deg(angles: list[float]) -> float:
    s = sum(math.sin(math.radians(a)) for a in angles)
    c = sum(math.cos(math.radians(a)) for a in angles)
    return math.degrees(math.atan2(s, c))


class DoaTracker:
    """Keeps the last few seconds of speaker azimuths (wall-clock stamped)."""

    def __init__(self, url: str, keep_s: float = 30.0) -> None:
        self.url = url
        self.keep_s = keep_s
        self.samples: deque[tuple[float, float]] = deque()  # (time.time(), azimuth_deg)

    def add(self, state: dict) -> None:
        doa = state.get("speaker_doa_rad")
        if doa is None or not state.get("speech_detected"):
            return
        now = time.time()
        self.samples.append((now, doa_to_azimuth_deg(doa, state.get("head_yaw_deg", 0.0))))
        while self.samples and self.samples[0][0] < now - self.keep_s:
            self.samples.popleft()

    def azimuth(self, t0: float, t1: float) -> float | None:
        """Mean speaker azimuth during [t0, t1] (wall clock), None if unknown."""
        hits = [a for t, a in self.samples if t0 <= t <= t1]
        return round(circular_mean_deg(hits), 1) if hits else None

    async def run(self) -> None:
        while True:
            try:
                async with websockets.connect(self.url) as ws:
                    log.info("DoA: connected to robot bridge %s", self.url)
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") == "state":
                            self.add(msg)
            except (OSError, websockets.WebSocketException):
                log.debug("DoA: robot bridge not reachable, retrying")
            await asyncio.sleep(2.0)
