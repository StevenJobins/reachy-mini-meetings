"""Voice activity detection that cuts the mic stream into utterances.

Feed it 16 kHz mono float32 chunks of any size. It emits:
  Segment(final=False)  after `first_partial_s`, then every `partial_every_s` while someone talks
                        -> live bubble text
  Segment(final=True)   after `silence_s` of silence (or `max_s`) -> final text + translation
  Segment(early=True)   (only if `early_s` > 0) after `early_s` of silence: the utterance as it would end
                        now, so slow work can start before the final; the final has the same audio length
                        unless speech resumed in between

`quiet` on a live segment = how long nobody has spoken at its end. `split()` ends the utterance at an earlier
point while it goes on (a sentence known to be complete), the rest becomes the next utterance.

With a neural VAD (vad.SileroVad, the default in captions.py) a frame is speech above `speech_prob`
(and keeps the utterance going above `keep_prob`). Without one, it falls back to loudness: noise floor
(EMA over quiet frames) + `margin_db`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 16000
FRAME = 512  # 32 ms (the frame size of the Silero VAD)


@dataclass
class Segment:
    id: int
    final: bool
    audio: np.ndarray   # everything from speech start (incl. pre-roll) until now
    t_start: float      # stream time in s
    t_end: float
    early: bool = False   # tentative end (see early_s), final is False
    quiet: float = 0.0    # live text only: seconds of silence at the end of `audio`
    age: float = 0.0      # early and final: how long ago `audio` ended (the silence waited for since)


@dataclass
class SegmenterCfg:
    margin_db: float = 12.0        # speech = this much above the noise floor
    min_level_db: float = -50.0    # never treat quieter frames as speech
    start_frames: int = 3          # consecutive loud frames to start (90 ms)
    silence_s: float = 0.8         # no speech this long -> utterance ends (shorter cuts sentences apart)
    speech_prob: float = 0.5       # neural VAD: speech starts above this probability ...
    keep_prob: float = 0.2         # ... and continues while above this one (distant voices dip between words)
    preroll_s: float = 0.3
    min_s: float = 0.4             # drop shorter blips (coughs, clicks)
    max_s: float = 15.0            # force a cut in monologues
    first_partial_s: float = 0.5   # first live text this early: the bubble appears quickly
    partial_every_s: float = 0.6
    early_s: float = 0.0           # > 0: emit a tentative end after this much silence (0 = off)


def frame_db(frame: np.ndarray) -> float:
    return 20 * np.log10(np.sqrt(np.mean(frame**2)) + 1e-9)


class Segmenter:
    def __init__(self, cfg: SegmenterCfg | None = None, vad=None) -> None:
        self.cfg = cfg or SegmenterCfg()
        self.vad = vad   # callable(frame) -> speech probability, or None for the loudness fallback
        self.prob = 0.0  # speech probability of the latest frame (neural VAD only)
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
        if self.vad is not None:
            self.prob = self.vad(f)
            loud = self.prob > (c.speech_prob if self._speech is None else c.keep_prob)
        else:
            db = frame_db(f)
            if self.noise_db is None:
                self.noise_db = db
            loud = db > max(self.noise_db + c.margin_db, c.min_level_db)
            if self._speech is None:
                # follow the room; slowly even when loud, so a steady hum stops counting as speech
                self.noise_db += (0.01 if loud else 0.05) * (db - self.noise_db)

        if self._speech is None:
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
        if c.early_s > 0 and self._quiet == max(1, round(c.early_s / dt)):
            audio = self._audio()
            if audio is not None:
                t_end = self._t_start + len(audio) / SAMPLE_RATE
                return Segment(self._next_id, False, audio, self._t_start, t_end, early=True, age=self._t - t_end)
        if self._since_partial >= (c.partial_every_s if self._had_partial else c.first_partial_s):
            self._since_partial = 0.0
            self._had_partial = True
            return Segment(self._next_id, False, np.concatenate(self._speech), self._t_start, self._t,
                           quiet=self._quiet * dt)
        return None

    def split(self, sid: int, n: int) -> bool:
        """Utterance `sid` ends after its first `n` samples (the audio of an early segment); what came after
        is the next utterance, or nothing if it was only silence. False if `sid` is no longer the current one."""
        k = n // FRAME
        if self._speech is None or sid != self._next_id or not 0 < k <= len(self._speech):
            return False
        self._next_id += 1
        self._speech = self._speech[k:]
        if self._quiet >= len(self._speech):   # nothing but silence since
            self._speech = None
            self._loud = 0
            return True
        self._t_start += k * FRAME / SAMPLE_RATE
        self._preroll_n = 0
        self._since_partial = 0.0
        self._had_partial = False
        return True

    def _audio(self) -> np.ndarray | None:
        """The utterance so far without its trailing silence, None if too short (cough, click)."""
        speech = self._speech
        trail = min(self._quiet, len(speech) - 1)
        if (len(speech) - trail - self._preroll_n) * FRAME / SAMPLE_RATE < self.cfg.min_s:
            return None
        return np.concatenate(speech[:len(speech) - trail])

    def _finish(self) -> Segment | None:
        audio = self._audio()
        self._speech = None
        self._loud = 0
        sid = self._next_id
        self._next_id += 1
        if audio is None:
            return None
        t_end = self._t_start + len(audio) / SAMPLE_RATE
        return Segment(sid, True, audio, self._t_start, t_end, age=self._t - t_end)
