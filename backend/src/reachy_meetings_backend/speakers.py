"""Who is talking, from the voice alone: one speaker embedding per utterance, grouped as they come in.

The camera (mouth movement) and the mic direction both failed on the robot (2026-11-06: no mouth in view moved,
the direction sat at 40-53 deg for everyone), so the subtitles' colours come from the voice. Works for people
out of the picture and doesn't care where Reachy looks.

Embedding: WavLM x-vector (microsoft/wavlm-base-plus-sv, ~380 MB, downloaded once), on the CPU in its own
thread: torch on the GPU next to MLX's Whisper made macOS abort MLX's command buffers (see captions.py, depth).
Grouping: cosine similarity to each known speaker's mean voice; above the threshold = that speaker, else a new one.
Short clips (< MIN_S) say little about the voice: no answer, the subtitle stays grey.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np

log = logging.getLogger(__name__)

MODEL = "microsoft/wavlm-base-plus-sv"
MIN_S = 1.0           # seconds of speech needed for an answer
MAX_S = 8.0           # longer clips: the last this many seconds (enough for the voice, bounded time)
THRESHOLD = 0.80      # same speaker above this (model card: 0.86 on clean speech; the room mic is far-field)
MAX_SPEAKERS = 6
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

    def add(self, emb: np.ndarray) -> int:
        """The speaker of this utterance (learning from it): a known one, a new one, or the closest when full."""
        e = emb / np.linalg.norm(emb)
        sid, _ = self.match(e)
        if sid is None and len(self.means) < self.max_speakers:
            self.means.append(e)
            self.counts.append(1)
            return len(self.means)
        if sid is None:
            sid = int(np.argmax(self.similarities(e))) + 1
        i = sid - 1
        n = min(self.counts[i], MEAN_OF - 1)
        m = (self.means[i] * n + e) / (n + 1)
        self.means[i] = m / np.linalg.norm(m)
        self.counts[i] += 1
        return sid


class VoiceIds:
    """Speaker ids for utterances (16 kHz mono float). Loads the model in the background; until then, None."""

    def __init__(self, threshold: float = THRESHOLD) -> None:
        self.clusters = SpeakerClusters(threshold)
        self.pool = ThreadPoolExecutor(1, thread_name_prefix="speakers")   # the model is used from one thread
        self.lock = threading.Lock()
        self.model = self.extractor = None
        self.pool.submit(self._load)

    def _load(self) -> None:
        try:
            import torch
            from transformers import AutoFeatureExtractor, WavLMForXVector

            torch.set_num_threads(4)
            self.extractor = AutoFeatureExtractor.from_pretrained(MODEL)
            self.model = WavLMForXVector.from_pretrained(MODEL).eval()
            log.info("Speakers: voice model %s ready", MODEL)
        except Exception as e:   # not installed, offline: subtitles without colours
            log.warning("Speakers: no voice model (%s), subtitles without speaker colours", e)

    def _embed(self, audio: np.ndarray) -> np.ndarray | None:
        if self.model is None or len(audio) < MIN_S * 16000:
            return None
        import torch

        audio = audio[-int(MAX_S * 16000):]
        inputs = self.extractor(audio, sampling_rate=16000, return_tensors="pt")
        with torch.no_grad():
            return self.model(**inputs).embeddings[0].numpy()

    def _identify(self, audio: np.ndarray, learn: bool) -> int | None:
        emb = self._embed(audio)
        if emb is None:
            return None
        with self.lock:
            if learn:
                sims = self.clusters.similarities(emb)
                sid = self.clusters.add(emb)
                log.info("Speakers: %.1f s -> %d (similarity %s)", len(audio) / 16000, sid,
                         " ".join(f"{s:.2f}" for s in sims) or "-")
                return sid
            return self.clusters.match(emb)[0]

    async def identify(self, audio: np.ndarray, learn: bool) -> int | None:
        """learn=False (a partial): only a known voice, nothing learnt; learn=True (the final): may add a speaker."""
        return await asyncio.wrap_future(self.pool.submit(self._identify, audio, learn))
