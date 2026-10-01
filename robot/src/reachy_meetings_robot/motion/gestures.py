"""Expressive gestures as time-parametric OFFSETS.

Gestures are added on top of the mirrored head pose instead of replacing it, so the robot can nod
while still following the remote user's gaze. Each gesture is a function of normalized time
s in [0, 1] returning (roll, pitch, yaw, antenna_left, antenna_right) offsets in degrees.

Add new gestures by writing a function and registering it in GESTURES.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass

Offsets = tuple[float, float, float, float, float]  # roll, pitch, yaw, ant_l, ant_r (deg)
GestureFn = Callable[[float], Offsets]


def _env(s: float) -> float:
    """Smooth 0 -> 1 -> 0 envelope so gestures start/end without jumps."""
    return math.sin(math.pi * s)


def nod(s: float) -> Offsets:            # "yes" / agreement
    return (0, 12 * _env(s) * math.sin(4 * math.pi * s), 0, 0, 0)


def shake(s: float) -> Offsets:          # "no"
    return (0, 0, 15 * _env(s) * math.sin(4 * math.pi * s), 0, 0)


def tilt_curious(s: float) -> Offsets:   # question / curiosity
    e = _env(s)
    return (15 * e, -5 * e, 0, 20 * e, -10 * e)


def antennas_happy(s: float) -> Offsets:  # laugh / joy: antennas wiggle
    w = 25 * _env(s) * math.sin(6 * math.pi * s)
    return (0, 0, 0, w, -w)


def antennas_sad(s: float) -> Offsets:
    e = _env(s)
    return (0, 8 * e, 0, -40 * e, -40 * e)


def attention(s: float) -> Offsets:      # "I want to say something" (raise hand equivalent)
    e = _env(s)
    return (0, -8 * e, 0, 35 * e, 35 * e)


def thinking(s: float) -> Offsets:
    e = _env(s)
    return (-10 * e, -10 * e, 10 * e, 15 * e, 0)


GESTURES: dict[str, GestureFn] = {
    "nod": nod,
    "shake": shake,
    "tilt_curious": tilt_curious,
    "antennas_happy": antennas_happy,
    "antennas_sad": antennas_sad,
    "attention": attention,
    "thinking": thinking,
}


@dataclass
class _Running:
    name: str
    fn: GestureFn
    t0: float
    duration: float


class GesturePlayer:
    """Plays at most one gesture at a time; a new gesture replaces the running one."""

    def __init__(self, default_duration: float = 0.6) -> None:
        self.default_duration = default_duration
        self._running: _Running | None = None

    def play(self, name: str, duration: float | None = None) -> None:
        if name not in GESTURES:
            raise KeyError(f"Unknown gesture '{name}'. Available: {sorted(GESTURES)}")
        self._running = _Running(name, GESTURES[name], time.monotonic(),
                                 duration or self.default_duration)

    @property
    def active(self) -> str | None:
        return self._running.name if self._running else None

    def step(self) -> Offsets:
        if self._running is None:
            return (0, 0, 0, 0, 0)
        s = (time.monotonic() - self._running.t0) / self._running.duration
        if s >= 1.0:
            self._running = None
            return (0, 0, 0, 0, 0)
        return self._running.fn(s)
