"""Configuration: default_config.yaml merged with an optional user override file."""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml


@dataclass
class RobotCfg:
    connection_mode: str = "auto"
    media_backend: str = "default"
    wake_up_on_start: bool = True
    sleep_on_exit: bool = True


@dataclass
class LimitsCfg:
    head_pitch_deg: float = 40
    head_roll_deg: float = 40
    head_yaw_deg: float = 180
    body_yaw_deg: float = 160
    max_head_body_yaw_delta_deg: float = 65
    head_z_mm: tuple[float, float] = (-20, 20)


@dataclass
class MirrorCfg:
    rate_hz: float = 50
    smoothing: float = 0.35
    deadband_deg: float = 0.8
    gain: tuple[float, float, float] = (1.0, 1.0, 1.0)
    stale_after_s: float = 0.5
    body_follow: bool = True
    head_max_vel_dps: float = 150
    head_max_acc_dps2: float = 800
    body_max_vel_dps: float = 90
    body_max_acc_dps2: float = 300


@dataclass
class GesturesCfg:
    default_duration_s: float = 0.6


@dataclass
class CameraCfg:
    rate_hz: float = 30
    attach_head_pose: bool = True


@dataclass
class AudioCfg:
    doa_rate_hz: float = 10
    tts_sample_rate: int = 16000


@dataclass
class BridgeCfg:
    host: str = "0.0.0.0"
    port: int = 8765
    state_rate_hz: float = 20


@dataclass
class Config:
    robot: RobotCfg = field(default_factory=RobotCfg)
    limits: LimitsCfg = field(default_factory=LimitsCfg)
    mirror: MirrorCfg = field(default_factory=MirrorCfg)
    gestures: GesturesCfg = field(default_factory=GesturesCfg)
    camera: CameraCfg = field(default_factory=CameraCfg)
    audio: AudioCfg = field(default_factory=AudioCfg)
    bridge: BridgeCfg = field(default_factory=BridgeCfg)


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _build(cls: type, data: dict[str, Any]):
    kwargs = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        val = data[f.name]
        sub = f.default_factory() if callable(f.default_factory) else None  # type: ignore[misc]
        if sub is not None and is_dataclass(sub):
            kwargs[f.name] = _build(type(sub), val or {})
        elif isinstance(val, list):
            kwargs[f.name] = tuple(val)
        else:
            kwargs[f.name] = val
    unknown = set(data) - {f.name for f in fields(cls)}
    if unknown:
        raise ValueError(f"Unknown config keys in {cls.__name__}: {sorted(unknown)}")
    return cls(**kwargs)


def load_config(path: str | Path | None = None) -> Config:
    """Load defaults, then apply the override file (if given)."""
    text = resources.files(__package__).joinpath("default_config.yaml").read_text()
    data = yaml.safe_load(text) or {}
    if path is not None:
        data = _deep_merge(data, yaml.safe_load(Path(path).read_text()) or {})
    return _build(Config, data)
