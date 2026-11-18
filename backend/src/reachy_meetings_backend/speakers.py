"""The voice cue: one speaker embedding per utterance, grouped as they come in.

Works for people out of the picture and doesn't care where Reachy looks. Only the embeddings are made here (in a
thread); which voice an utterance is decided in fusion.py, together with the mouths and the mic direction.

Long utterances (the segmenter cuts at 15 s, turn-taking without a pause stays one utterance) are embedded in
windows of WIN_S: if the windows sound like different people, the utterance is "mixed" and only its main voice
counts.

Embedding: CAM++ (3D-Speaker, trained on VoxCeleb; ONNX, 28 MB, downloaded once), on the CPU with onnxruntime,
35 ms for 3 s. Until 2026-11-18 it was WavLM (microsoft/wavlm-base-plus-sv, 380 MB, needs torch): in the room two
different people were 0.69-0.85 alike and the same person 0.84-0.98, too close to tell similar voices apart.
scripts/eval_voices.py compares the models on 40 speakers as a far microphone hears them.
Grouping: cosine similarity to each known speaker's mean voice (fusion.py has the numbers).
Short clips (< MIN_S) say little about the voice: no answer, the subtitle has no name.
"""

from __future__ import annotations

import asyncio
import logging
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# ONNX speaker models (80 mel bands in, one embedding out), from the sherpa-onnx model collection:
# key -> (file, samples as int16 values, window). Compared in scripts/eval_voices.py.
ONNX_MODELS = {
    "campplus": ("3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx", False, "povey"),
    "eres2netv2": ("3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx", False, "povey"),
    "wespeaker-campplus": ("wespeaker_en_voxceleb_CAM++.onnx", True, "hamming"),
    "resnet34": ("wespeaker_en_voxceleb_resnet34_LM.onnx", True, "hamming"),
}
ONNX_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
MODEL_DIR = Path.home() / ".cache" / "reachy-meetings"
MODEL = "campplus"
MIN_S = 1.0           # seconds of speech needed for an answer
NEW_S = 2.5           # ... and for a new speaker: a short clip is a poor picture of a voice (2026-11-11: a 1.1 s
                      # clip became speaker 1, the same person's next sentences matched it at 0.50 and became 2)
MAX_S = 8.0           # partials: the last this many seconds (enough for the voice, bounded time)
THRESHOLD = 0.40      # may be that speaker above this (fusion.py has the ranges this is read from)
WIN_S = 3.0           # finals of 2 * WIN_S or more are embedded in windows of about this length
WIN_SAME = 0.35       # windows at least this similar are the same voice (below: someone else took over): two 3 s
                      # clips of one speaker are 0.45 at the 5th percentile, of two speakers 0.34 at the 95th
MAX_SPEAKERS = 8
MEAN_OF = 20          # a speaker's mean voice follows the last ~this many utterances


class SpeakerClusters:
    """Online grouping of voice embeddings. Pure numpy, no model."""

    def __init__(self, threshold: float = THRESHOLD, max_speakers: int = MAX_SPEAKERS) -> None:
        self.threshold, self.max_speakers = threshold, max_speakers
        self.means: list[np.ndarray] = []   # unit vectors, index + 1 = speaker id
        self.counts: list[int] = []

    def similarities(self, emb: np.ndarray) -> list[float]:
        e = emb / np.linalg.norm(emb)
        return [float(m @ e) for m in self.means]

    def match(self, emb: np.ndarray) -> tuple[int | None, float]:
        """(speaker id, similarity) of the closest known voice above the threshold, else (None, best)."""
        sims = self.similarities(emb)
        if not sims:
            return None, 0.0
        best = int(np.argmax(sims))
        return (best + 1 if sims[best] >= self.threshold else None), sims[best]

    def add(self, emb: np.ndarray, may_create: bool = True) -> int | None:
        """The speaker of this utterance (learning from it): a known one, a new one, or the closest when full.
        may_create=False (a short clip): only a known one, else None."""
        e = emb / np.linalg.norm(emb)
        sid, _ = self.match(e)
        if sid is None and not may_create:
            return None
        if sid is None and len(self.means) < self.max_speakers:
            self.means.append(e)
            self.counts.append(1)
            return len(self.means)
        if sid is None:
            sid = int(np.argmax(self.similarities(e))) + 1
        self.learn(sid, e)
        return sid

    def create(self, emb: np.ndarray) -> int:
        """A new speaker with this voice (the closest known one when full)."""
        e = emb / np.linalg.norm(emb)
        if len(self.means) >= self.max_speakers:
            sid = int(np.argmax(self.similarities(e))) + 1
            self.learn(sid, e)
            return sid
        self.means.append(e)
        self.counts.append(1)
        return len(self.means)

    def learn(self, sid: int, emb: np.ndarray) -> None:
        """Speaker `sid`'s mean voice moves towards this one."""
        e = emb / np.linalg.norm(emb)
        i = sid - 1
        n = min(self.counts[i], MEAN_OF - 1)
        m = (self.means[i] * n + e) / (n + 1)
        self.means[i] = m / np.linalg.norm(m)
        self.counts[i] += 1


@dataclass
class VoiceObs:
    """The voice of one utterance. emb = the main voice (unit vector); dur = seconds of it; mixed = some windows
    sounded like someone else (two people in one utterance); windows = similarities of the windows to the main one,
    for the log."""
    emb: np.ndarray
    dur: float
    mixed: bool = False
    windows: list[float] = field(default_factory=list)


def main_voice(embs: list[np.ndarray], win_s: float) -> VoiceObs:
    """The windows of one utterance -> its main voice: the windows that sound like the most typical one (the window
    most similar to all others), averaged."""
    e = np.array([x / np.linalg.norm(x) for x in embs])
    if len(e) == 1:
        return VoiceObs(e[0], win_s)
    sims = e @ e.T
    centre = int(np.argmax(sims.sum(1)))
    same = sims[centre] >= WIN_SAME
    m = e[same].mean(0)
    m /= np.linalg.norm(m)
    return VoiceObs(m, win_s * int(same.sum()), mixed=not same.all(), windows=[round(float(x), 2) for x in e @ m])


def fbank(audio: np.ndarray, window: str = "povey", bins: int = 80) -> np.ndarray:
    """Log mel filterbank energies as Kaldi computes them (25 ms frames every 10 ms, 16 kHz, no dither, 20 Hz to
    8 kHz), [frames, bins]. In numpy, so the voice model needs neither torch nor a feature library; checked against
    kaldi-native-fbank in tests/test_voice_model.py."""
    n, shift, nfft = 400, 160, 512
    if len(audio) < n:
        audio = np.pad(audio, (0, n - len(audio)))
    idx = np.arange(n)[None, :] + shift * np.arange(1 + (len(audio) - n) // shift)[:, None]
    frames = audio[idx].astype(np.float64)
    frames -= frames.mean(1, keepdims=True)
    frames = np.concatenate([frames[:, :1] * (1 - 0.97), frames[:, 1:] - 0.97 * frames[:, :-1]], 1)
    a = 2 * np.pi * np.arange(n) / (n - 1)
    frames *= (0.5 - 0.5 * np.cos(a)) ** 0.85 if window == "povey" else 0.54 - 0.46 * np.cos(a)
    power = np.abs(np.fft.rfft(frames, nfft)[:, : nfft // 2]) ** 2
    return np.log(np.maximum(power @ _mel_bank(bins, nfft).T, np.finfo(np.float32).eps)).astype(np.float32)


def _mel_bank(bins: int, nfft: int, low: float = 20.0, high: float = 8000.0) -> np.ndarray:
    def mel(f):
        return 1127.0 * np.log(1 + f / 700.0)

    edges = mel(low) + (mel(high) - mel(low)) / (bins + 1) * np.arange(bins + 2)
    m = mel(16000 / nfft * np.arange(nfft // 2))[None, :]
    left, centre, right = edges[:-2, None], edges[1:-1, None], edges[2:, None]
    return np.clip(np.minimum((m - left) / (centre - left), (right - m) / (right - centre)), 0, None)


def model_path(key: str) -> Path:
    """The ONNX file of a voice model, downloaded once (~30 MB)."""
    name = ONNX_MODELS[key][0]
    path = MODEL_DIR / name
    for known in (path, MODEL_DIR / "eval" / "models" / name):
        if known.exists():
            return known
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    urllib.request.urlretrieve(ONNX_URL + urllib.request.quote(name), tmp)
    tmp.rename(path)
    return path


class OnnxVoice:
    """audio (16 kHz mono float, -1..1) -> voice embedding. Not thread-safe by contract: use it from one thread."""

    def __init__(self, key: str = MODEL) -> None:
        import onnxruntime as ort

        _, self.int16, self.window = ONNX_MODELS[key]
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2   # Whisper and the face finder need the other cores
        self.session = ort.InferenceSession(str(model_path(key)), opts, providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name

    def __call__(self, audio: np.ndarray) -> np.ndarray:
        feats = fbank(audio * 32768.0 if self.int16 else audio, self.window)
        feats -= feats.mean(0, keepdims=True)
        return self.session.run(None, {self.input: feats[None]})[0][0]


class VoiceIds:
    """Voice embeddings of utterances (16 kHz mono float). Loads the model in the background; until then, None.
    `clusters` (the known voices) is used by fusion.py, on the event loop; this class only embeds."""

    def __init__(self, threshold: float = THRESHOLD, model: str = MODEL) -> None:
        self.clusters = SpeakerClusters(threshold)
        self.pool = ThreadPoolExecutor(1, thread_name_prefix="speakers")   # the model is used from one thread
        self.name, self.model = model, None
        self.pool.submit(self._load)

    def _load(self) -> None:
        try:
            self.model = OnnxVoice(self.name)
            log.info("Speakers: voice model %s ready", ONNX_MODELS[self.name][0])
        except Exception as e:   # no download, no onnxruntime: subtitles without speaker names
            log.warning("Speakers: no voice model (%s), subtitles without speaker names", e)

    def _embed(self, audio: np.ndarray, windows: bool) -> VoiceObs | None:
        if self.model is None or len(audio) < MIN_S * 16000:
            return None
        n = int(len(audio) / 16000 // WIN_S) if windows else 1
        if n < 2:
            audio = audio[-int(MAX_S * 16000):]
            return main_voice([self.model(audio)], len(audio) / 16000)
        k = len(audio) // n   # equal windows
        return main_voice([self.model(audio[i * k:(i + 1) * k]) for i in range(n)], k / 16000)

    async def embed(self, audio: np.ndarray, windows: bool = False) -> VoiceObs | None:
        """windows=True (a final): long audio in windows, to notice a second voice in it."""
        return await asyncio.wrap_future(self.pool.submit(self._embed, audio, windows))
