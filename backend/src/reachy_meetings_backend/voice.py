"""The remote user's translated voice: text -> macOS speech synthesis (`say`) -> the Reachy speaker.

The robot's speaker is a USB audio output on the Mac (same device as the mic), so we play to it directly.
Voice per language: the best installed one. macOS ships only compact voices; much better ones (Premium /
Enhanced) are free in System Settings > Accessibility > Spoken Content > System voice > Manage Voices, and are
picked automatically once installed. `--voice de=Markus,en=Ava` overrides.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

# Novelty and Eloquence voices sound like toys; Anna is the default German one the user found grating.
SILLY = {"Albert", "Bad News", "Bahh", "Bells", "Boing", "Bubbles", "Cellos", "Good News", "Jester", "Junior",
         "Organ", "Superstar", "Trinoids", "Whisper", "Wobble", "Zarvox", "Fred", "Kathy", "Ralph"}
ELOQUENCE = ("Eddy", "Flo", "Grandma", "Grandpa", "Reed", "Rocko", "Sandy", "Shelley")


def installed_voices() -> list[tuple[str, str]]:
    """[(voice name, locale like 'de_DE')] from `say -v ?`."""
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, check=True).stdout
    voices = []
    for line in out.splitlines():
        m = re.match(r"(.+?)\s+([a-z]{2}_[A-Z]{2})\s+#", line)
        if m:
            voices.append((m.group(1).strip(), m.group(2)))
    return voices


def rank(name: str) -> int:
    if "Premium" in name:
        return 0
    if any(k in name for k in ("Enhanced", "Erweitert", "Verbessert")):
        return 1
    if name.split(" (")[0] in ELOQUENCE:
        return 3
    if name == "Anna":
        return 4
    return 2


def pick_voice(lang: str, voices: list[tuple[str, str]], preferred_region: str = "") -> str | None:
    cands = [(rank(n), loc != f"{lang}_{preferred_region}", n) for n, loc in voices
             if loc.startswith(lang + "_") and n not in SILLY]
    return min(cands)[2] if cands else None


def find_output_device(name: str) -> int | None:
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0 and name.lower() in d["name"].lower():
            return i
    return None


class VoiceOut:
    REGION = {"de": "DE", "en": "US", "fr": "FR", "es": "ES", "it": "IT", "pt": "BR"}

    def __init__(self, device: str = "Reachy", overrides: str = "") -> None:
        self.device_name = device
        self.voices = installed_voices()
        self.overrides = dict(kv.split("=", 1) for kv in overrides.split(",") if "=" in kv)
        self.lock = asyncio.Lock()   # one sentence at a time

    def voice(self, lang: str) -> str | None:
        return self.overrides.get(lang) or pick_voice(lang, self.voices, self.REGION.get(lang, ""))

    def _synth(self, text: str, voice: str | None) -> tuple[np.ndarray, int]:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "s.wav"
            cmd = ["say", "--file-format=WAVE", "--data-format=LEI16@22050", "-o", str(path)]
            subprocess.run(cmd + (["-v", voice] if voice else []) + ["--", text], check=True)
            with wave.open(str(path)) as w:
                return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768, w.getframerate()

    async def speak(self, text: str, lang: str, on_start=None) -> float:
        """Say `text` on the robot speaker; returns the duration in s. on_start(duration) right before playback."""
        async with self.lock:
            voice = self.voice(lang)
            audio, sr = await asyncio.to_thread(self._synth, text, voice)
            dev = find_output_device(self.device_name)   # looked up each time: the robot may be re-plugged
            if dev is None:
                log.warning("No output device matching %r, using the default speaker", self.device_name)
            dur = len(audio) / sr
            log.info("Speaking (%s, %s, %.1f s): %s", lang, voice or "default voice", dur, text)
            if on_start:
                on_start(dur)
            await asyncio.to_thread(lambda: (sd.play(audio, sr, device=dev), sd.wait()))
            return dur
