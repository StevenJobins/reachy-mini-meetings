"""Pure-logic tests: run without robot, daemon or SDK.   pytest -q"""

import time

import numpy as np
import pytest

from reachy_meetings_robot.bridge.protocol import (
    GestureMsg,
    HeadPoseMsg,
    ProtocolError,
    SayMsg,
    encode,
    parse,
)
from reachy_meetings_robot.config import LimitsCfg, load_config
from reachy_meetings_robot.motion.controller import MotionController
from reachy_meetings_robot.motion.gestures import GESTURES, GesturePlayer
from reachy_meetings_robot.motion.head_mirror import HeadMirror, HeadsetPose
from reachy_meetings_robot.safety import HeadTarget, body_yaw_to_follow, clamp_head
from reachy_meetings_robot.state import StateStore

LIM = LimitsCfg()


# ---------------------------------------------------------------- config
def test_default_config_loads():
    cfg = load_config()
    assert cfg.mirror.rate_hz > 0
    assert (cfg.limits.head_pitch_up_deg, cfg.limits.head_pitch_down_deg, cfg.limits.head_roll_deg) == (35, 20, 15)


def test_config_override(tmp_path):
    p = tmp_path / "o.yaml"
    p.write_text("mirror:\n  rate_hz: 25\n")
    cfg = load_config(p)
    assert cfg.mirror.rate_hz == 25
    assert cfg.mirror.smoothing == load_config().mirror.smoothing  # untouched keys keep defaults


def test_config_rejects_typos(tmp_path):
    p = tmp_path / "o.yaml"
    p.write_text("mirror:\n  rate_hzz: 25\n")
    with pytest.raises(ValueError):
        load_config(p)


# ---------------------------------------------------------------- safety
def test_clamp_pitch_roll():
    h = clamp_head(HeadTarget(roll=90, pitch=-90), body_yaw_deg=0, lim=LIM)
    assert h.roll == 15 and h.pitch == -35   # looking up stops at 35° (people standing close by)
    h = clamp_head(HeadTarget(roll=-90, pitch=90), body_yaw_deg=0, lim=LIM)
    assert h.roll == -15 and h.pitch == 20   # looking down (table) stops at 20°


def test_head_body_delta():
    h = clamp_head(HeadTarget(yaw=120), body_yaw_deg=0, lim=LIM)
    assert h.yaw == 65


def test_body_follows_head():
    body = body_yaw_to_follow(head_yaw_deg=100, body_yaw_deg=0, lim=LIM)
    assert body == pytest.approx(35)
    assert body_yaw_to_follow(10, 0, LIM) == 0  # within delta -> body stays
    assert body_yaw_to_follow(500, 0, LIM) == 160  # body limit


# ---------------------------------------------------------------- mirror
def test_mirror_converges_and_decays():
    cfg = load_config().mirror
    m = HeadMirror(cfg)
    m.push(HeadsetPose(0, 10, 30, time.time()))
    for _ in range(100):
        out = m.step()
    assert out.yaw == pytest.approx(30, abs=1) and out.pitch == pytest.approx(10, abs=1)
    m._t_received -= cfg.stale_after_s + 1  # simulate lost headset
    for _ in range(200):
        out = m.step()
    assert abs(out.yaw) < 1 and not m.active


# ---------------------------------------------------------------- gestures
@pytest.mark.parametrize("name", sorted(GESTURES))
def test_gestures_start_and_end_at_zero(name):
    fn = GESTURES[name]
    assert np.allclose(fn(0.0), 0, atol=1e-6)
    assert np.allclose(fn(1.0), 0, atol=1e-6)


def test_gesture_player_finishes():
    p = GesturePlayer()
    p.play("nod", duration=0.05)
    assert p.active == "nod"
    time.sleep(0.06)
    assert p.step() == (0, 0, 0, 0, 0) and p.active is None
    with pytest.raises(KeyError):
        p.play("does_not_exist")


# ---------------------------------------------------------------- controller (no robot I/O)
def test_controller_compose_respects_limits():
    mc = MotionController(mini=None, cfg=load_config(), state=StateStore())
    mc.mirror.push(HeadsetPose(0, 80, 170, time.time()))
    for _ in range(200):
        head, body, _ = mc.compose()
    assert abs(head.pitch) <= 40
    assert abs(head.yaw - body) <= 65 + 1e-9
    assert body > 0  # body turned to follow


def test_controller_compose_limits_speed():
    """A violent head snap must not become a jerk: velocity stays within the configured limits."""
    cfg = load_config()
    mc = MotionController(mini=None, cfg=cfg, state=StateStore())
    dt = 1 / cfg.mirror.rate_hz
    prev_head, prev_body = HeadTarget(), 0.0
    for i in range(300):
        mc.mirror.push(HeadsetPose(0, 30 if i < 150 else -20, 150 if i < 150 else -120, time.time()))
        head, body, _ = mc.compose()
        assert abs(head.yaw - prev_head.yaw) / dt <= cfg.mirror.head_max_vel_dps + 1e-6
        assert abs(head.pitch - prev_head.pitch) / dt <= cfg.mirror.head_max_vel_dps + 1e-6
        assert abs(body - prev_body) / dt <= cfg.mirror.body_max_vel_dps + 1e-6
        assert abs(head.yaw - body) <= 65 + 1e-9
        prev_head, prev_body = head, body


# ---------------------------------------------------------------- protocol
def test_protocol_roundtrip():
    msg = parse(encode("head_pose", roll=1, pitch=2, yaw=3, t=4))
    assert msg == HeadPoseMsg(1, 2, 3, 4)
    assert parse(encode("gesture", name="nod")) == GestureMsg("nod")


def test_protocol_audio_roundtrip():
    x = np.sin(np.linspace(0, 20, 1600)).astype(np.float32) * 0.5
    y = parse(encode("say", **SayMsg.from_float32(x, 16000).__dict__)).to_float32()
    assert np.allclose(x, y, atol=1e-3)


@pytest.mark.parametrize("raw", ["not json", '{"type": "unknown"}', '{"type": "gesture"}'])
def test_protocol_rejects_bad(raw):
    with pytest.raises(ProtocolError):
        parse(raw)
