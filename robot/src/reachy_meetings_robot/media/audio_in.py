"""Microphone capture + direction of arrival (DoA).

- Audio chunks go to the backend for speech-to-text / translation (speech bubbles).
- DoA tells us roughly where the current speaker is -> the headset can anchor the speech bubble,
  and the robot can optionally glance at the speaker.

SDK facts (Pollen docs): get_audio_sample() -> float32 (samples, 2) at 16 kHz;
get_DoA() -> (angle_rad, speech_detected), 0 = left, pi/2 = front/back, pi = right.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import numpy as np

from ..config import AudioCfg
from ..state import StateStore

log = logging.getLogger(__name__)

AudioCallback = Callable[[np.ndarray, int], None]  # (samples, sample_rate)


class MicListener:
    def __init__(self, mini, cfg: AudioCfg, state: StateStore) -> None:
        self.mini = mini
        self.cfg = cfg
        self.state = state
        self._subscribers: list[AudioCallback] = []
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def subscribe(self, cb: AudioCallback) -> None:
        self._subscribers.append(cb)

    def start(self) -> None:
        self.mini.media.start_recording()
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._audio_loop, name="mic", daemon=True),
            threading.Thread(target=self._doa_loop, name="doa", daemon=True),
        ]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2)
        self.mini.media.stop_recording()

    def _audio_loop(self) -> None:
        sr = self.mini.media.get_input_audio_samplerate()
        while not self._stop.is_set():
            samples = self.mini.media.get_audio_sample()
            if samples is None or len(samples) == 0:
                time.sleep(0.005)
                continue
            for cb in self._subscribers:
                try:
                    cb(samples, sr)
                except Exception:
                    log.exception("audio subscriber failed")

    def _doa_loop(self) -> None:
        period = 1.0 / self.cfg.doa_rate_hz
        while not self._stop.is_set():
            try:
                doa = self.mini.media.get_DoA()
                if doa is not None:
                    angle, speech = doa
                    self.state.update(speaker_doa_rad=float(angle), speech_detected=bool(speech))
            except Exception:
                log.exception("DoA read failed")
            time.sleep(period)
