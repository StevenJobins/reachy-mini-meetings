"""Clamp commanded poses to Reachy Mini's limits.

The SDK clamps as well, but clamping here keeps the state we report to the headset identical to
what the robot actually does (important for reprojection).
Limits (Pollen docs, Core Concepts): head pitch/roll ±40°, head yaw ±180°, body yaw ±160°,
max 65° between head yaw and body yaw.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import LimitsCfg


@dataclass(frozen=True)
class HeadTarget:
    """Head orientation in degrees (robot head frame) + vertical offset in mm."""

    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    z_mm: float = 0.0


def clamp_head(target: HeadTarget, body_yaw_deg: float, lim: LimitsCfg) -> HeadTarget:
    roll = float(np.clip(target.roll, -lim.head_roll_deg, lim.head_roll_deg))
    pitch = float(np.clip(target.pitch, -lim.head_pitch_deg, lim.head_pitch_deg))
    yaw = float(np.clip(target.yaw, -lim.head_yaw_deg, lim.head_yaw_deg))
    d = lim.max_head_body_yaw_delta_deg
    yaw = float(np.clip(yaw, body_yaw_deg - d, body_yaw_deg + d))
    z = float(np.clip(target.z_mm, *lim.head_z_mm))
    return HeadTarget(roll, pitch, yaw, z)


def clamp_body_yaw(body_yaw_deg: float, lim: LimitsCfg) -> float:
    return float(np.clip(body_yaw_deg, -lim.body_yaw_deg, lim.body_yaw_deg))


def body_yaw_to_follow(head_yaw_deg: float, body_yaw_deg: float, lim: LimitsCfg) -> float:
    """Minimal body rotation so the head yaw stays within the allowed head/body delta."""
    d = lim.max_head_body_yaw_delta_deg
    if head_yaw_deg > body_yaw_deg + d:
        body_yaw_deg = head_yaw_deg - d
    elif head_yaw_deg < body_yaw_deg - d:
        body_yaw_deg = head_yaw_deg + d
    return clamp_body_yaw(body_yaw_deg, lim)
