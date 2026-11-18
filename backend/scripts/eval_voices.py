"""Which voice model tells people apart best, and where the thresholds belong: measured, not guessed.

    python scripts/eval_voices.py fetch        # LibriSpeech test-clean (346 MB) + the ONNX models, once
    python scripts/eval_voices.py bench        # every model: equal error rate, similarity ranges, time
    python scripts/eval_voices.py fixture      # tests/data/voices.npz: real embeddings for tests/test_scenarios.py

Speech: LibriSpeech test-clean (40 speakers, read English, CC BY 4.0), cut to the lengths utterances have in a
meeting (1.5, 3, 6 s). "room" = the same clips as a far microphone hears them: reverb (RT60 0.5 s, direct sound
-3..+6 dB against the reverb, as 1.5-3 m away in an office) and noise 15 dB under the speech. This is a simulation:
the robot's own mic chain (beam forming, noise suppression) is not in it. For that, replay through the robot
(`bench --wav-dir`, clips recorded with the robot mic, one folder per speaker).

What is measured, per model:
  eer        equal error rate over single clips: same speaker against different speaker
  known      what the backend does: a speaker's mean voice from 3 clips, then new clips against it
             (same p05 = 5 % of the speaker's own clips are below this; other p95/p99 = 5 % / 1 % of other people's
             clips are above this; "closest" = the same against the most similar other speaker, the hard case)
  ms         time per 3 s clip on the CPU

Data and models go to ~/.cache/reachy-meetings/eval (never into the repo).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

from reachy_meetings_backend import speakers

CACHE = Path.home() / ".cache" / "reachy-meetings" / "eval"
LIBRI = CACHE / "librispeech" / "LibriSpeech" / "test-clean"
LIBRI_URL = "https://www.openslr.org/resources/12/test-clean.tar.gz"
SR = 16000
CLIPS = 16            # per speaker
LENGTHS = (1.5, 3.0, 6.0)


# ------------------------------------------------------------------ speech
def read_audio(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32)


def librispeech(clips: int = CLIPS) -> dict[str, list[np.ndarray]]:
    """speaker -> utterances (16 kHz float), the longest ones first cut out so every clip has 6 s of speech."""
    cache = CACHE / f"librispeech_{clips}.npz"
    if cache.exists():
        return _group(np.load(cache))
    out: dict[str, list[np.ndarray]] = {}
    for spk in sorted(p for p in LIBRI.iterdir() if p.is_dir()):
        files = sorted(spk.rglob("*.flac"), key=lambda f: -f.stat().st_size)[:clips]
        out[spk.name] = [read_audio(f)[: int(8 * SR)] for f in files]
    np.savez(cache, **{f"{s}/{i}": a for s, clips_ in out.items() for i, a in enumerate(clips_)})
    return out


def _group(z) -> dict[str, list[np.ndarray]]:
    out: dict[str, list[np.ndarray]] = {}
    for k in sorted(z.files, key=lambda k: (k.split("/")[0], int(k.split("/")[1]))):
        out.setdefault(k.split("/")[0], []).append(z[k])
    return out


def room(audio: np.ndarray, rng: np.random.Generator, rt60: float = 0.5, snr_db: float = 15.0) -> np.ndarray:
    """The clip as a far microphone hears it: direct sound + a decaying reverb tail + noise."""
    n = int(rt60 * SR)
    t = np.arange(n) / SR
    tail = rng.standard_normal(n) * np.exp(-6.9 * t / rt60)
    tail[: int(0.004 * SR)] = 0                        # the first reflections come a few ms after the direct sound
    drr_db = rng.uniform(-3, 6)
    tail *= 10 ** (-drr_db / 20) / np.sqrt((tail**2).sum())
    from scipy.signal import fftconvolve

    h = np.concatenate([[1.0], tail])
    wet = fftconvolve(audio, h)[: len(audio)]
    noise = rng.standard_normal(len(wet))
    noise = np.convolve(noise, np.ones(8) / 8, mode="same")   # more low than high, like a room
    noise *= np.sqrt((wet**2).mean() / (noise**2).mean()) * 10 ** (-snr_db / 20)
    out = wet + noise
    return (out / (np.abs(out).max() + 1e-9) * 0.3).astype(np.float32)


# ------------------------------------------------------------------ models
class WavLM:
    name = "wavlm-base-plus-sv"

    def __init__(self) -> None:
        import torch
        from transformers import AutoFeatureExtractor, WavLMForXVector

        torch.set_num_threads(4)
        self.torch = torch
        self.extractor = AutoFeatureExtractor.from_pretrained("microsoft/wavlm-base-plus-sv")
        self.model = WavLMForXVector.from_pretrained("microsoft/wavlm-base-plus-sv").eval()

    def __call__(self, audio: np.ndarray) -> np.ndarray:
        inputs = self.extractor([audio], sampling_rate=SR, return_tensors="pt")
        with self.torch.no_grad():
            return self.model(**inputs).embeddings.numpy()[0]


class Onnx:
    """The ONNX speaker models of speakers.py (fbank features in numpy, onnxruntime)."""

    def __init__(self, key: str) -> None:
        self.name = key
        self.model = speakers.OnnxVoice(key)

    def __call__(self, audio: np.ndarray) -> np.ndarray:
        return self.model(audio)


def load(name: str):
    return WavLM() if name == "wavlm" else Onnx(name)


def models(names: list[str]):
    for n in names:
        try:
            yield load(n)
        except Exception as e:  # noqa: BLE001 - a model that is not there is skipped
            print(f"!! {n}: {e}")


# ------------------------------------------------------------------ numbers
def unit(x: np.ndarray) -> np.ndarray:
    return x / np.linalg.norm(x, axis=-1, keepdims=True)


def eer(same: np.ndarray, other: np.ndarray) -> tuple[float, float]:
    """(equal error rate, threshold there)."""
    thr = np.sort(np.concatenate([same, other]))
    miss = np.searchsorted(np.sort(same), thr, side="left") / len(same)          # same speaker below the threshold
    false = 1 - np.searchsorted(np.sort(other), thr, side="left") / len(other)   # others at or above it
    i = int(np.argmin(np.abs(miss - false)))
    return float((miss[i] + false[i]) / 2), float(thr[i])


_clips: dict[tuple, tuple[list[np.ndarray], np.ndarray]] = {}


def clips_for(speech: dict[str, list[np.ndarray]], length: float, far: bool, seed: int = 0):
    """(clips, speaker index): the same clips for every model, made once."""
    key = (id(speech), length, far, seed)
    if key not in _clips:
        rng = np.random.default_rng(seed)
        out, who = [], []
        for i, (_, clips) in enumerate(speech.items()):
            for a in clips:
                a = a[int(0.3 * SR): int((0.3 + length) * SR)]
                out.append(room(a, rng) if far else a)
                who.append(i)
        _clips[key] = (out, np.array(who))
    return _clips[key]


def embed_all(model, speech: dict[str, list[np.ndarray]], length: float, far: bool, seed: int = 0):
    """(embeddings [n, dim] unit, speaker index [n], seconds per clip)."""
    clips, who = clips_for(speech, length, far, seed)
    t0 = time.time()
    embs = [model(a) for a in clips]
    return unit(np.array(embs)), who, (time.time() - t0) / len(embs)


def measure(e: np.ndarray, who: np.ndarray, enrol: int = 3) -> dict:
    sims = e @ e.T
    iu = np.triu_indices(len(e), 1)
    same_pair = who[iu[0]] == who[iu[1]]
    rate, thr = eer(sims[iu][same_pair], sims[iu][~same_pair])
    spk = np.unique(who)
    means = unit(np.array([e[who == s][:enrol].mean(0) for s in spk]))        # the first clips make the mean voice
    test = np.array([i for s in spk for i in np.flatnonzero(who == s)[enrol:]])
    s = e[test] @ means.T                                                     # [clips, speakers]
    own = s[np.arange(len(test)), who[test]]
    mask = np.ones_like(s, bool)
    mask[np.arange(len(test)), who[test]] = False
    other = s[mask]
    closest = np.where(mask, s, -1).max(1)                                    # the most similar other speaker
    return {"eer": rate, "eer_thr": thr, "same_p05": np.percentile(own, 5), "same_p50": np.percentile(own, 50),
            "other_p95": np.percentile(other, 95), "other_p99": np.percentile(other, 99),
            "closest_p50": np.percentile(closest, 50), "closest_p95": np.percentile(closest, 95),
            "wrong": float((closest >= own).mean())}   # a clip closer to someone else's mean than to its own


def bench(args) -> None:
    speech = wav_dir(Path(args.wav_dir)) if args.wav_dir else librispeech()
    print(f"{len(speech)} speakers, {sum(map(len, speech.values()))} clips\n", flush=True)
    print(f"{'model':28s} {'clips':12s} {'eer':>6s} {'at':>5s} | {'same p05':>8s} {'p50':>5s} | {'other p95':>9s} "
          f"{'p99':>5s} | {'closest p50':>11s} {'p95':>5s} | {'wrong':>6s} | {'ms':>5s}")
    for model in models(args.models.split(",")):
        for far in ([False] if args.wav_dir else [False, True]):
            for length in LENGTHS:
                e, who, dt = embed_all(model, speech, length, far)
                m = measure(e, who)
                print(f"{model.name:28s} {('room' if far else 'close') + f' {length:.1f} s':12s} {m['eer']:6.1%} "
                      f"{m['eer_thr']:5.2f} | {m['same_p05']:8.2f} {m['same_p50']:5.2f} | {m['other_p95']:9.2f} "
                      f"{m['other_p99']:5.2f} | {m['closest_p50']:11.2f} {m['closest_p95']:5.2f} | {m['wrong']:6.1%} | "
                      f"{dt * 1000:5.0f}", flush=True)
        print(flush=True)


def wav_dir(root: Path) -> dict[str, list[np.ndarray]]:
    """Own recordings: one folder per speaker with audio files (any format ffmpeg reads)."""
    return {d.name: [read_audio(f) for f in sorted(d.iterdir()) if f.is_file()] for d in sorted(root.iterdir()) if d.is_dir()}


def fixture(args) -> None:
    """Real embeddings for the scenario tests (tests/sim.py): every speaker as the far microphone hears them, in
    clips of 1.5 s and 3 s cut from different places of their recordings."""
    speech = librispeech()
    model = load(args.model)
    out = Path(args.out) if args.out else Path(__file__).resolve().parents[1] / "tests" / "data" / "voices.npz"
    out.parent.mkdir(exist_ok=True)
    rng = np.random.default_rng(1)
    data = {}
    for length, starts, per in ((1.5, (0.3, 2.2, 4.1, 6.0), 32), (3.0, (0.3, 4.0), 24)):
        embs, who = [], []
        for i, clips in enumerate(speech.values()):
            cuts = [a[int(s * SR): int((s + length) * SR)] for a in clips for s in starts]
            cuts = [c for c in cuts if len(c) >= int(length * SR)][:per]
            embs += [model(room(c, rng)) for c in cuts]
            who += [i] * len(cuts)
        data[f"room_{length}"] = unit(np.array(embs)).astype(np.float16)
        data[f"who_{length}"] = np.array(who, np.int16)
    np.savez_compressed(out, model=np.array(model.name), **data)
    print(f"{out}: {out.stat().st_size / 1e6:.1f} MB, {len(speech)} speakers, "
          + ", ".join(f"{k} {v.shape}" for k, v in data.items() if k.startswith("room")))


def fetch(_args) -> None:
    if not LIBRI.exists():
        tar = CACHE / "librispeech" / "test-clean.tar.gz"
        tar.parent.mkdir(parents=True, exist_ok=True)
        print(f"{LIBRI_URL} -> {tar.parent} (346 MB)")
        urllib.request.urlretrieve(LIBRI_URL, tar)
        subprocess.run(["tar", "xzf", tar.name], cwd=tar.parent, check=True)
        tar.unlink()
    for key in speakers.ONNX_MODELS:
        print(key, speakers.model_path(key))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch").set_defaults(fn=fetch)
    b = sub.add_parser("bench")
    b.add_argument("--models", default="wavlm," + ",".join(speakers.ONNX_MODELS))
    b.add_argument("--wav-dir", help="own recordings instead of LibriSpeech: one folder per speaker")
    b.set_defaults(fn=bench)
    f = sub.add_parser("fixture")
    f.add_argument("--model", default=speakers.MODEL)
    f.add_argument("--out", help="another file than tests/data/voices.npz")
    f.set_defaults(fn=fixture)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
