"""JSON message protocol between robot <-> xr-client / backend (WebSocket, one JSON per message).

Every message: {"type": <str>, ...fields}. Keep this file in sync with the xr-client and backend.

Incoming (to robot)
  head_pose      {"roll": deg, "pitch": deg, "yaw": deg, "t": headset_time_s}
                 Angles already in ROBOT head frame: roll + = tilt right?, pitch + = look down?,
                 yaw + = look left?  -> TODO verify signs on the real robot, then fix this line.
  gesture        {"name": str, "duration": s | null}
  say            {"pcm16_b64": base64 int16 mono PCM, "sample_rate": int}
  stop_speaking  {}

Outgoing (from robot)
  hello          {"version": str, "gestures": [str]}
  state          RobotState fields (see state.py)
  error          {"message": str}
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

import numpy as np

PROTOCOL_VERSION = "0.1"


@dataclass
class HeadPoseMsg:
    roll: float
    pitch: float
    yaw: float
    t: float = 0.0


@dataclass
class GestureMsg:
    name: str
    duration: float | None = None


@dataclass
class SayMsg:
    pcm16_b64: str
    sample_rate: int = 16000

    def to_float32(self) -> np.ndarray:
        pcm = np.frombuffer(base64.b64decode(self.pcm16_b64), dtype=np.int16)
        return pcm.astype(np.float32) / 32768.0

    @staticmethod
    def from_float32(samples: np.ndarray, sample_rate: int) -> SayMsg:
        pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes()
        return SayMsg(base64.b64encode(pcm).decode("ascii"), sample_rate)


@dataclass
class StopSpeakingMsg:
    pass


Incoming = HeadPoseMsg | GestureMsg | SayMsg | StopSpeakingMsg

_INCOMING: dict[str, type] = {
    "head_pose": HeadPoseMsg,
    "gesture": GestureMsg,
    "say": SayMsg,
    "stop_speaking": StopSpeakingMsg,
}


class ProtocolError(ValueError):
    pass


def parse(raw: str | bytes) -> Incoming:
    try:
        data: dict[str, Any] = json.loads(raw)
        kind = data.pop("type")
        cls = _INCOMING[kind]
        return cls(**data)
    except (KeyError, TypeError, json.JSONDecodeError) as e:
        raise ProtocolError(f"Invalid message: {e!r}") from e


def encode(kind: str, **fields: Any) -> str:
    return json.dumps({"type": kind, **fields})
