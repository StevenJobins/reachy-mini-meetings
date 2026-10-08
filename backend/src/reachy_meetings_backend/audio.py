"""Audio sources, both yielding 16 kHz mono float32 chunks into an asyncio queue.

MicSource:  any input device. The Reachy Mini (Lite) shows up as a USB audio device on the Mac,
            so we read the room audio directly; macOS lets the robot process record in parallel.
FileSource: a WAV file played in real time, for testing without a room.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import wave

import numpy as np
import sounddevice as sd

from .segmenter import SAMPLE_RATE

log = logging.getLogger(__name__)


def find_input_device(name: str | None) -> int | None:
    """Index of the first input device whose name contains `name` (case-insensitive)."""
    if not name:
        return None
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and name.lower() in d["name"].lower():
            return i
    return None


# Unplug + replug of the robot: PortAudio keeps its device list from start-up and the input callback just
# stops (no exception), so captions, speech detection and room audio stopped for good (code review 2026-10-08).
# MicSource watches the callback and reopens; a failed playback (voice.py) asks for the same via RESTART.
RESTART = threading.Event()
_pa_lock = threading.Lock()


def reinit_portaudio() -> None:
    """Fresh device list (after re-plugging): all open streams must be closed before."""
    with _pa_lock:
        sd._terminate()
        sd._initialize()


class MicSource:
    def __init__(self, device: str | None, block_s: float = 0.04, silent_s: float = 2.0) -> None:   # small blocks: low audio latency
        self.name = device
        self.device = find_input_device(device)
        if device and self.device is None:
            log.warning("No input device matching %r, using the default mic", device)
        self.block = int(block_s * SAMPLE_RATE)
        self.silent_s = silent_s

    async def run(self, out: asyncio.Queue) -> None:
        loop = asyncio.get_running_loop()
        last = [time.time()]

        def callback(indata, frames, t, status):
            if status:
                log.debug("mic: %s", status)
            last[0] = time.time()
            loop.call_soon_threadsafe(out.put_nowait, indata[:, 0].copy())

        first = True
        while True:
            if not first:
                await asyncio.to_thread(reinit_portaudio)
                self.device = find_input_device(self.name)
                if self.name and self.device is None:
                    await asyncio.sleep(2)   # still unplugged
                    continue
            first = False
            RESTART.clear()
            name = sd.query_devices(self.device, "input")["name"]
            last[0] = time.time()
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                blocksize=self.block, device=self.device, callback=callback):
                log.info("Listening on %r", name)
                while time.time() - last[0] < self.silent_s and not RESTART.is_set():
                    await asyncio.sleep(0.5)
            log.warning("Mic %r stopped delivering audio (unplugged?) or restart requested: reopening", name)


class FileSource:
    def __init__(self, path: str, block_s: float = 0.04) -> None:
        with wave.open(path) as w:
            sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
            raw = w.readframes(w.getnframes())
        if width != 2:
            raise ValueError("only 16-bit PCM WAV supported")
        x = np.frombuffer(raw, np.int16).reshape(-1, ch).mean(axis=1) / 32768.0
        if sr != SAMPLE_RATE:
            n = int(len(x) * SAMPLE_RATE / sr)
            x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
        self.audio = x.astype(np.float32)
        self.block_s = block_s

    async def run(self, out: asyncio.Queue) -> None:
        block = int(self.block_s * SAMPLE_RATE)
        tail = np.zeros(SAMPLE_RATE, np.float32)  # 1 s silence so the last utterance ends
        audio = np.concatenate([self.audio, tail])
        for i in range(0, len(audio), block):
            out.put_nowait(audio[i:i + block])
            await asyncio.sleep(self.block_s)
        log.info("File finished")
