"""Shared robot state, written by the control loop and read by the bridge / camera."""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field


@dataclass
class RobotState:
    t: float = field(default_factory=time.time)
    # Commanded (after clamping) – this is what the headset should assume for reprojection.
    head_roll_deg: float = 0.0
    head_pitch_deg: float = 0.0
    head_yaw_deg: float = 0.0
    head_z_mm: float = 0.0
    body_yaw_deg: float = 0.0
    antennas_deg: tuple[float, float] = (0.0, 0.0)
    # Context
    active_gesture: str | None = None
    speaking: bool = False          # robot is playing TTS
    mirror_active: bool = False     # fresh headset pose received recently
    speaker_doa_rad: float | None = None
    speech_detected: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class StateStore:
    """Thread-safe holder for the latest RobotState."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = RobotState()

    def get(self) -> RobotState:
        with self._lock:
            return RobotState(**self._state.to_dict())

    def update(self, **kwargs) -> None:
        with self._lock:
            for k, v in kwargs.items():
                setattr(self._state, k, v)
            self._state.t = time.time()
