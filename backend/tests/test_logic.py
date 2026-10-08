"""Pure-logic tests: no mic, no Whisper model, no network.   pytest -q"""

from itertools import pairwise

import numpy as np

from reachy_meetings_backend.captions import NoiseGate
from reachy_meetings_backend.doa import DoaTracker, circular_mean_deg, doa_to_head_deg
from reachy_meetings_backend.segmenter import SAMPLE_RATE, Segmenter, SegmenterCfg
from reachy_meetings_backend.stt import plausible
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


def test_tracker_window_ignores_chip_speech_flag_and_adds_head_yaw():
    # the chip's speech flag is unreliable (false while people talk), so readings count without it
    tr = DoaTracker("http://unused")
    tr.add({"angle": 0.0, "speech_detected": False}, 30, now=10)   # speaker left, head turned left
    assert tr.direction(0, 100) == (90, 120)
    assert tr.direction(11, 100) == (None, None)
    tr.add({"angle": None, "speech_detected": True}, 0, now=10)   # daemon without a reading yet
    assert len(tr.samples) == 1


# ---------------------------------------------------------------- summary
def test_parse_summary_plain_and_fenced():
    raw = ('{"summary": ["Budget first"], "actions": [{"who": "Lisa", "what": "show prototype", "when": "Friday"}], '
           '"next_steps": ["Decide on the venue"]}')
    want = {"summary": ["Budget first"], "actions": [{"who": "Lisa", "what": "show prototype", "when": "Friday"}],
            "next_steps": ["Decide on the venue"]}
    assert parse_summary(raw) == want
    assert parse_summary("Here you go:\n```json\n" + raw + "\n```") == want


def test_parse_summary_tolerates_shapes_and_rejects_garbage():
    assert parse_summary('{"summary": [], "actions": ["call Bob", ""]}') == {
        "summary": [], "actions": [{"who": "", "what": "call Bob", "when": ""}], "next_steps": []}
    assert parse_summary('{"next_steps": ["pick a date"]}') == {"summary": [], "actions": [], "next_steps": ["pick a date"]}
    assert parse_summary("no json here") is None
    assert parse_summary('{"summary": [') is None
    assert parse_summary('{"summary": [], "actions": []}') is None


# ---------------------------------------------------------------- hallucination filter
def test_plausible_filters_whisper_hallucinations():
    assert plausible("Wie heißt deine Mutter?", 0.01, -0.3, 1.2)
    assert not plausible("Untertitelung des ZDF, 2020", 0.01, -0.2, 1.1)
    assert not plausible("Vielen Dank.", 0.4, -0.3, 1.0)           # unsure -> fake
    assert plausible("Vielen Dank.", 0.05, -0.2, 1.0)              # clearly spoken -> keep
    assert not plausible("1,0, 1,0, 1,0, 1,0, 1,0, 1,0", 0.1, -0.4, 3.1)
    assert plausible("irgendwas", 0.7, -0.2, 1.0)                  # confident text despite no-speech: keep
    assert not plausible("irgendwas", 0.7, -1.2, 1.0)              # unsure AND likely silence: drop
    assert not plausible("Gemurmel", 0.1, -2.5, 1.0)               # very unsure: drop


def test_segmenter_with_vad_ignores_loud_noise():
    # fake VAD: speech only where the tone is; a loud click burst before it must not start an utterance
    audio = np.concatenate([noise(1), np.ones(1600, np.float32) * 0.5, noise(1), tone(1.5), noise(1.2)])
    marks = np.concatenate([np.zeros(16000 + 1600 + 16000), np.ones(24000), np.zeros(19200)])
    pos = {"i": 0}

    def vad(frame):
        p = marks[pos["i"]:pos["i"] + len(frame)].mean()
        pos["i"] += len(frame)
        return p

    finals = [s for s in feed(Segmenter(vad=vad), audio) if s.final]
    assert len(finals) == 1
    assert finals[0].t_start > 1.7   # tone at 2.1 s minus 0.3 s pre-roll; the click (1.0 s) is ignored


# ---------------------------------------------------------------- noise gate
def test_noise_gate_quiet_in_pauses_full_in_speech_no_jumps():
    g = NoiseGate(-14)
    one = np.ones(640, np.float32)
    quiet = g(one, False, 0.0)
    assert quiet.max() < 0.25                        # -14 dB in a pause
    up = g(one, True, 0.04)
    assert abs(up[-1] - 1.0) < 1e-6 and up[0] < 0.25  # ramps up within the frame, no jump
    tail = g(one, False, 0.2)
    assert tail.min() > 0.99                          # sentence tail kept (hold)
    later = [g(one, False, 0.2 + 0.04 * i)[-1] for i in range(1, 20)]
    assert all(b <= a + 1e-6 for a, b in pairwise(later)) and later[-1] < 0.25   # smooth release


def test_pick_voice_prefers_premium_and_avoids_anna():
    from reachy_meetings_backend.voice import pick_voice

    voices = [("Anna", "de_DE"), ("Flo (Deutsch (Deutschland))", "de_DE"), ("Petra (Premium)", "de_DE"),
              ("Bells", "en_US"), ("Samantha", "en_US"), ("Daniel", "en_GB")]
    assert pick_voice("de", voices, "DE") == "Petra (Premium)"
    assert pick_voice("de", voices[:2], "DE") == "Flo (Deutsch (Deutschland))"
    assert pick_voice("en", voices, "US") == "Samantha"
    assert pick_voice("ja", voices) is None


# ---------------------------------------------------------------- tunnel
def test_tunnel_url_regex_skips_cloudflare_api_host():
    from reachy_meetings_backend.tunnel import URL_RE

    assert URL_RE.search(b"failed to request https://api.trycloudflare.com/tunnel") is None
    assert URL_RE.search(b"|  https://quiet-river-1.trycloudflare.com  |").group() == b"https://quiet-river-1.trycloudflare.com"


def test_tunnel_message_signature_verifies(tmp_path, monkeypatch):
    import base64
    import json

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    from reachy_meetings_backend import tunnel

    monkeypatch.setattr(tunnel, "KEY_PATH", tmp_path / "k.pem")
    key = tunnel.signing_key()
    assert tunnel.signing_key().private_numbers() == key.private_numbers()   # reloaded, not regenerated
    m = json.loads(tunnel.signed_message("wss://a-b.trycloudflare.com", key, 123))
    raw = base64.b64decode(m["sig"])
    pub = serialization.load_der_public_key(base64.b64decode(m["key"]))
    sig = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    pub.verify(sig, b"wss://a-b.trycloudflare.com|123", ec.ECDSA(hashes.SHA256()))   # raises if wrong
