"""Pure-logic tests: no mic, no Whisper model, no network.   pytest -q"""

import numpy as np

from reachy_meetings_backend.doa import DoaTracker, circular_mean_deg, doa_to_azimuth_deg
from reachy_meetings_backend.segmenter import SAMPLE_RATE, Segmenter, SegmenterCfg

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
    assert doa_to_azimuth_deg(np.pi / 2, 0) == 0        # front
    assert doa_to_azimuth_deg(0.0, 0) == 90             # left
    assert doa_to_azimuth_deg(np.pi, 0) == -90          # right
    assert doa_to_azimuth_deg(np.pi / 2, 30) == 30      # head turned left


def test_circular_mean_wraps():
    assert abs(abs(circular_mean_deg([170, -170])) - 180) < 1e-6


def test_tracker_window_and_speech_flag():
    tr = DoaTracker("ws://unused")
    tr.add({"speaker_doa_rad": 0.0, "speech_detected": False, "head_yaw_deg": 0})
    assert tr.azimuth(0, 1e12) is None
    tr.add({"speaker_doa_rad": 0.0, "speech_detected": True, "head_yaw_deg": 0})
    assert tr.azimuth(0, 1e12) == 90
    assert tr.azimuth(0, 1) is None
