"""Speaker output: plays the remote user's translated speech (TTS) in the meeting room.

push_audio_sample() is non-blocking (Pollen docs), so a worker thread plays utterances one after
another and sleeps for their duration. While playing, StateStore.speaking=True, which makes the
MotionController animate the antennas.
"""

from __future__ import annotations

import logging
import queue
import threading
import time

import numpy as np
from scipy.signal import resample

from ..state import StateStore

log = logging.getLogger(__name__)


class SpeakerPlayer:
    def __init__(self, mini, state: StateStore) -> None:
        self.mini = mini
        self.state = state
        self._queue: queue.Queue[tuple[np.ndarray, int]] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def say(self, samples: np.ndarray, sample_rate: int) -> None:
        """Queue float32 mono/stereo audio for playback."""
        self._queue.put((samples.astype(np.float32), sample_rate))

    def clear(self) -> None:
        """Drop everything not yet played (e.g. remote user interrupts)."""
        while not self._queue.empty():
            self._queue.get_nowait()

    def start(self) -> None:
        self.mini.media.start_playing()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="speaker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self.mini.media.stop_playing()

    def _run(self) -> None:
        out_sr = self.mini.media.get_output_audio_samplerate()
        while not self._stop.is_set():
            try:
                samples, sr = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if sr != out_sr:
                samples = resample(samples, int(len(samples) * out_sr / sr)).astype(np.float32)
            if samples.ndim == 1:
                samples = samples[:, None]
            self.state.update(speaking=True)
            self.mini.media.push_audio_sample(samples)
            time.sleep(len(samples) / out_sr)
            if self._queue.empty():
                self.state.update(speaking=False)
