"""The voice cue: one speaker embedding per utterance, grouped as they come in.

Works for people out of the picture and doesn't care where Reachy looks. Only the embeddings are made here (in a
thread); which voice an utterance is decided in fusion.py, together with the mouths and the mic direction.

Long utterances (the segmenter cuts at 15 s, turn-taking without a pause stays one utterance) are embedded in
windows of WIN_S: if the windows sound like different people, the utterance is "mixed" and only its main voice
counts (2026-11-18 test: 12-15 s clips that may hold two people pulled speaker 1's mean voice towards everyone,
until every utterance matched it at 0.82-0.98).

Embedding: WavLM x-vector (microsoft/wavlm-base-plus-sv, ~380 MB, downloaded once), on the CPU in its own
thread: torch on the GPU next to MLX's Whisper made macOS abort MLX's command buffers (see captions.py, depth).
Grouping: cosine similarity to each known speaker's mean voice; above the threshold = that speaker, else a new one.
Short clips (< MIN_S) say little about the voice: no answer, the subtitle stays grey.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)

MODEL = "microsoft/wavlm-base-plus-sv"
MIN_S = 1.0           # seconds of speech needed for an answer
NEW_S = 2.5           # ... and for a new speaker: a short clip is a poor picture of a voice (2026-11-11: a 1.1 s
                      # clip became speaker 1, the same person's next sentences matched it at 0.50 and became 2)
MAX_S = 8.0           # partials: the last this many seconds (enough for the voice, bounded time)
THRESHOLD = 0.80      # same speaker above this (model card: 0.86 on clean speech; the room mic is far-field)
WIN_S = 3.0           # finals of 2 * WIN_S or more are embedded in windows of about this length
WIN_SAME = 0.70       # windows at least this similar are the same voice (below: someone else took over). A guess:
                      # 2026-11-18 different voices met at 0.69-0.79, the same voice at 0.84-0.98; tune from the
                      # "windows" in the Speakers log lines
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


class VoiceIds:
    """Voice embeddings of utterances (16 kHz mono float). Loads the model in the background; until then, None.
    `clusters` (the known voices) is used by fusion.py, on the event loop; this class only embeds."""

    def __init__(self, threshold: float = THRESHOLD) -> None:
        self.clusters = SpeakerClusters(threshold)
        self.pool = ThreadPoolExecutor(1, thread_name_prefix="speakers")   # the model is used from one thread
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

    def _embed(self, audio: np.ndarray, windows: bool) -> VoiceObs | None:
        if self.model is None or len(audio) < MIN_S * 16000:
            return None
        import torch

        n = int(len(audio) / 16000 // WIN_S) if windows else 1
        if n < 2:
            audio = audio[-int(MAX_S * 16000):]
            batch, win_s = [audio], len(audio) / 16000
        else:   # equal windows, one batch (no padding needed)
            k = len(audio) // n
            batch, win_s = [audio[i * k:(i + 1) * k] for i in range(n)], k / 16000
        inputs = self.extractor(batch, sampling_rate=16000, return_tensors="pt")
        with torch.no_grad():
            embs = self.model(**inputs).embeddings.numpy()
        return main_voice(list(embs), win_s)

    async def embed(self, audio: np.ndarray, windows: bool = False) -> VoiceObs | None:
        """windows=True (a final): long audio in windows, to notice a second voice in it."""
        return await asyncio.wrap_future(self.pool.submit(self._embed, audio, windows))
