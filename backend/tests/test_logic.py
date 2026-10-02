"""Pure-logic tests: no mic, no Whisper model, no network.   pytest -q"""

import numpy as np

from reachy_meetings_backend.doa import DoaTracker, circular_mean_deg, doa_to_head_deg
from reachy_meetings_backend.segmenter import SAMPLE_RATE, Segmenter, SegmenterCfg
from reachy_meetings_backend.summary import parse_summary

rng = np.random.default_rng(0)


def noise(s: float, level: float = 0.001) -> np.ndarray:
    return (rng.standard_normal(int(s * SAMPLE_RATE)) * level).astype(np.float32)


def tone(s: float, amp: float = 0.2) -> np.ndarray:
    t = np.arange(int(s * SAMPLE_RATE)) / SAMPLE_RATE
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32) + noise(s)


def feed(seg: Segmenter, audio: np.ndarray, block: int = 1600):
    out = []
    for i in range(0, len(audio), block):
        out += seg.push(audio[i:i + block])
    return out


# ---------------------------------------------------------------- segmenter
def test_silence_gives_nothing():
    assert feed(Segmenter(), noise(3.0)) == []


def test_one_utterance_with_partials_then_final():
    out = feed(Segmenter(), np.concatenate([noise(1.0), tone(2.0), noise(1.0)]))
    finals = [s for s in out if s.final]
    partials = [s for s in out if not s.final]
    assert len(finals) == 1 and len(partials) >= 1
    f = finals[0]
    assert all(p.id == f.id for p in partials)
    assert 0.6 < f.t_start < 1.0                       # starts near the tone, with pre-roll
    assert 1.9 < len(f.audio) / SAMPLE_RATE < 2.6      # trailing silence trimmed


def test_first_partial_comes_early():
    out = feed(Segmenter(), np.concatenate([noise(1.0), tone(2.0), noise(1.0)]), block=480)
    first = next(s for s in out if not s.final)
    assert first.t_end - first.t_start < 1.0    # first_partial_s 0.5 + pre-roll 0.3 + start frames


def test_two_utterances_get_new_ids():
    audio = np.concatenate([noise(1), tone(1), noise(1), tone(1), noise(1)])
    finals = [s for s in feed(Segmenter(), audio) if s.final]
    assert [s.id for s in finals] == [0, 1]


def test_short_click_dropped():
    out = feed(Segmenter(), np.concatenate([noise(1), tone(0.15), noise(1)]))
    assert [s for s in out if s.final] == []


def test_monologue_is_cut_at_max():
    seg = Segmenter(SegmenterCfg(max_s=3.0))
    finals = [s for s in feed(seg, np.concatenate([noise(1), tone(7), noise(1)])) if s.final]
    assert len(finals) >= 2


def test_threshold_adapts_to_noisy_room():
    seg = Segmenter()
    feed(seg, noise(3.0, level=0.02))   # loud room hum
    assert seg.noise_db > -40
    assert not seg.active


# ---------------------------------------------------------------- DoA
def test_doa_mapping():
    assert doa_to_head_deg(np.pi / 2) == 0        # front
    assert doa_to_head_deg(0.0) == 90             # left
    assert doa_to_head_deg(np.pi) == -90          # right


def test_circular_mean_wraps():
    assert abs(abs(circular_mean_deg([170, -170])) - 180) < 1e-6


def test_tracker_window_speech_flag_and_head_yaw():
    tr = DoaTracker("http://unused")
    tr.add({"angle": 0.0, "speech_detected": False}, 0, now=10)
    assert tr.direction(0, 100) == (None, None)
    tr.add({"angle": 0.0, "speech_detected": True}, 30, now=10)   # speaker left, head turned left
    assert tr.direction(0, 100) == (90, 120)
    assert tr.direction(11, 100) == (None, None)
    tr.add({"angle": None, "speech_detected": True}, 0, now=10)   # daemon without a reading yet
    assert len(tr.samples) == 1


# ---------------------------------------------------------------- summary
def test_parse_summary_plain_and_fenced():
    raw = '{"summary": ["Budget first"], "actions": [{"who": "Lisa", "what": "show prototype"}]}'
    want = {"summary": ["Budget first"], "actions": [{"who": "Lisa", "what": "show prototype"}]}
    assert parse_summary(raw) == want
    assert parse_summary("Here you go:\n```json\n" + raw + "\n```") == want


def test_parse_summary_tolerates_shapes_and_rejects_garbage():
    assert parse_summary('{"summary": [], "actions": ["call Bob", ""]}') == {
        "summary": [], "actions": [{"who": "", "what": "call Bob"}]}
    assert parse_summary("no json here") is None
    assert parse_summary('{"summary": [') is None
    assert parse_summary('{"summary": [], "actions": []}') is None
