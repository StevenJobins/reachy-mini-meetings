"""Energy-based voice activity detection that cuts the mic stream into utterances.

Feed it 16 kHz mono float32 chunks of any size. It emits:
  Segment(final=False)  after `first_partial_s`, then every `partial_every_s` while someone talks
                        -> live bubble text
  Segment(final=True)   after `silence_s` of silence (or `max_s`) -> final text + translation

The speech threshold follows the room: noise floor (EMA over quiet frames) + `margin_db`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 16000
FRAME = 480  # 30 ms


@dataclass
class Segment:
    id: int
    final: bool
    audio: np.ndarray   # everything from speech start (incl. pre-roll) until now
    t_start: float      # stream time in s
    t_end: float


@dataclass
class SegmenterCfg:
    margin_db: float = 12.0        # speech = this much above the noise floor
    min_level_db: float = -50.0    # never treat quieter frames as speech
    start_frames: int = 3          # consecutive loud frames to start (90 ms)
    silence_s: float = 0.45        # quiet this long -> utterance ends
    preroll_s: float = 0.3
    min_s: float = 0.4             # drop shorter blips (coughs, clicks)
    max_s: float = 15.0            # force a cut in monologues
    first_partial_s: float = 0.5   # first live text this early: the bubble appears quickly
    partial_every_s: float = 0.6


def frame_db(frame: np.ndarray) -> float:
    return 20 * np.log10(np.sqrt(np.mean(frame**2)) + 1e-9)


class Segmenter:
    def __init__(self, cfg: SegmenterCfg | None = None) -> None:
        self.cfg = cfg or SegmenterCfg()
        self.noise_db: float | None = None  # set from the first frame
        self._pending = np.zeros(0, np.float32)
        self._preroll: list[np.ndarray] = []
        self._speech: list[np.ndarray] | None = None
        self._loud = 0
        self._quiet = 0
        self._t = 0.0            # stream time at the end of the last processed frame
        self._t_start = 0.0
        self._preroll_n = 0
        self._since_partial = 0.0
        self._had_partial = False
        self._next_id = 0

    @property
    def active(self) -> bool:
        return self._speech is not None

    def push(self, chunk: np.ndarray) -> list[Segment]:
        self._pending = np.concatenate([self._pending, chunk.astype(np.float32).ravel()])
        out: list[Segment] = []
        n = len(self._pending) // FRAME
        for i in range(n):
            seg = self._frame(self._pending[i * FRAME:(i + 1) * FRAME])
            if seg:
                out.append(seg)
        self._pending = self._pending[n * FRAME:]
        return out

    def flush(self) -> Segment | None:
        return self._finish() if self.active else None

    def _frame(self, f: np.ndarray) -> Segment | None:
        c = self.cfg
        dt = FRAME / SAMPLE_RATE
        self._t += dt
        db = frame_db(f)
        if self.noise_db is None:
            self.noise_db = db
        loud = db > max(self.noise_db + c.margin_db, c.min_level_db)

        if self._speech is None:
            # follow the room; slowly even when loud, so a steady hum stops counting as speech
            self.noise_db += (0.01 if loud else 0.05) * (db - self.noise_db)
            self._preroll.append(f)
            self._preroll = self._preroll[-int(c.preroll_s / dt) - c.start_frames:]
            self._loud = self._loud + 1 if loud else 0
            if self._loud >= c.start_frames:
                self._speech = list(self._preroll)
                self._preroll_n = len(self._speech) - c.start_frames
                self._preroll = []
                self._t_start = self._t - len(self._speech) * dt
                self._quiet = 0
                self._since_partial = 0.0
                self._had_partial = False
            return None

        self._speech.append(f)
        self._quiet = 0 if loud else self._quiet + 1
        self._since_partial += dt
        dur = self._t - self._t_start
        if self._quiet * dt >= c.silence_s or dur >= c.max_s:
            return self._finish()
        if self._since_partial >= (c.partial_every_s if self._had_partial else c.first_partial_s):
            self._since_partial = 0.0
            self._had_partial = True
            return Segment(self._next_id, False, np.concatenate(self._speech), self._t_start, self._t)
        return None

    def _finish(self) -> Segment | None:
        speech, self._speech = self._speech, None
        self._loud = 0
        sid = self._next_id
        self._next_id += 1
        # trailing silence is not part of the utterance
        trail = min(self._quiet, len(speech) - 1)
        audio = np.concatenate(speech[:len(speech) - trail])
        if (len(speech) - trail - self._preroll_n) * FRAME / SAMPLE_RATE < self.cfg.min_s:
            return None
        return Segment(sid, True, audio, self._t_start, self._t_start + len(audio) / SAMPLE_RATE)
