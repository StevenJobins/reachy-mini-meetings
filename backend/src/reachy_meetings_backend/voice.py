"""The remote user's translated voice: text -> macOS speech synthesis (`say`) -> the Reachy speaker.

The robot's speaker is a USB audio output on the Mac (same device as the mic), so we play to it directly.
Voice per language: the best installed one. macOS ships only compact voices; much better ones (Premium /
Enhanced) are free in System Settings > Accessibility > Spoken Content > System voice > Manage Voices, and are
picked automatically once installed. `--voice de=Markus,en=Ava` overrides; `system` = the macOS system voice
(the only way to get a Siri voice: Siri voices are not listed by `say -v ?`, but `say` without -v uses the
system voice, so set System Settings > Accessibility > Spoken Content > System voice to the Siri voice).

Synthesis runs in this process (AppKit NSSpeechSynthesizer, one synthesizer per voice, kept loaded): `say`
starts a process and loads the voice on every call, 0.9 s (Daniel) to 2.1 s (system voice) regardless of the
text length; in-process it takes 0.03-0.15 s after the first call (measured 2026-10-08). The system voice
goes through AppKit only if it gives exactly the same sound as `say` (checked once at start), else `say`.
"""

from __future__ import annotations

import asyncio
import logging
import re
import struct
import subprocess
import tempfile
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

# Novelty and Eloquence voices sound like toys; Anna is the default German one the user found grating.
SILLY = {"Albert", "Bad News", "Bahh", "Bells", "Boing", "Bubbles", "Cellos", "Good News", "Jester", "Junior",
         "Organ", "Superstar", "Trinoids", "Whisper", "Wobble", "Zarvox", "Fred", "Kathy", "Ralph"}
# Chosen by ear (2026-10-08). The page switches between them (Voice button); "system" = the macOS system voice,
# set to Siri. Note: `say -v <unknown name>` silently falls back to another voice, so Siri can't be named.
GENDER_VOICES = {"male": {"de": "Viktor", "en": "Daniel"}, "female": {"de": "system", "en": "Samantha"}}
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


def read_aiff(data: bytes) -> tuple[np.ndarray, int]:
    """16-bit PCM AIFF / AIFF-C (what NSSpeechSynthesizer writes) -> (float32 mono-or-first-channel, rate)."""
    if data[:4] != b"FORM" or data[8:12] not in (b"AIFF", b"AIFC"):
        raise ValueError("not an AIFF file")
    pos, channels, rate, little, pcm = 12, 1, 0, False, b""
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack(">I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"COMM":
            channels, _, bits = struct.unpack(">hIh", body[:8])
            exp, mant = struct.unpack(">HQ", body[8:18])   # 80-bit extended float
            rate = round(mant * 2.0 ** ((exp & 0x7FFF) - 16383 - 63))
            comp = body[18:22] if data[8:12] == b"AIFC" else b"NONE"
            if bits != 16 or comp not in (b"NONE", b"twos", b"sowt"):
                raise ValueError(f"unsupported AIFF format {bits} bit {comp!r}")
            little = comp == b"sowt"
        elif cid == b"SSND":
            offset = struct.unpack(">I", body[:4])[0]
            pcm = body[8 + offset:]
        pos += 8 + size + (size & 1)
    x = np.frombuffer(pcm[:len(pcm) // 2 * 2], "<i2" if little else ">i2")
    return x.reshape(-1, channels)[:, 0].astype(np.float32) / 32768, rate


class AppKitSynth:
    """NSSpeechSynthesizer, used from one thread only (run loop polled while it writes the file)."""

    def __init__(self) -> None:
        from AppKit import NSSpeechSynthesizer
        from Foundation import NSDate, NSRunLoop, NSURL

        self._ns, self._date, self._loop, self._url = NSSpeechSynthesizer, NSDate, NSRunLoop, NSURL
        self.ids = {NSSpeechSynthesizer.attributesForVoice_(v)["VoiceName"]: v
                    for v in NSSpeechSynthesizer.availableVoices()}
        self.synths: dict = {}
        self.dir = tempfile.TemporaryDirectory()

    def __call__(self, text: str, voice: str | None) -> tuple[np.ndarray, int]:
        if voice not in self.synths:
            self.synths[voice] = self._ns.alloc().initWithVoice_(self.ids[voice] if voice else None)
        s, path = self.synths[voice], Path(self.dir.name) / "s.aiff"
        path.unlink(missing_ok=True)
        if not s.startSpeakingString_toURL_(text, self._url.fileURLWithPath_(str(path))):
            raise OSError("NSSpeechSynthesizer refused the text")
        t0 = time.time()
        while s.isSpeaking() and time.time() - t0 < 20:
            self._loop.currentRunLoop().runUntilDate_(self._date.dateWithTimeIntervalSinceNow_(0.005))
        return read_aiff(path.read_bytes())


def say_synth(text: str, voice: str | None) -> tuple[np.ndarray, int]:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "s.wav"
        cmd = ["say", "--file-format=WAVE", "--data-format=LEI16@22050", "-o", str(path)]
        subprocess.run(cmd + (["-v", voice] if voice else []) + ["--", text], check=True)
        with wave.open(str(path)) as w:
            return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768, w.getframerate()


class VoiceOut:
    REGION = {"de": "DE", "en": "US", "fr": "FR", "es": "ES", "it": "IT", "pt": "BR"}

    def __init__(self, device: str = "Reachy", overrides: str = "") -> None:
        self.device_name = device
        self.voices = installed_voices()
        self.overrides = dict(kv.split("=", 1) for kv in overrides.split(",") if "=" in kv)
        self.lock = asyncio.Lock()   # one sentence at a time
        self.gender = "male"
        self.thread = ThreadPoolExecutor(1)   # all synthesis on one thread (AppKit objects stay on it)
        self.appkit: AppKitSynth | None = None
        self.appkit_system = False
        self.thread.submit(self._init_appkit)

    def _init_appkit(self) -> None:
        try:
            self.appkit = AppKitSynth()
            # Siri as system voice may not be reachable through AppKit: use it there only if it sounds the same
            a, b = self.appkit("Test.", None)[0], say_synth("Test.", None)[0]
            self.appkit_system = len(a) == len(b) and bool(np.allclose(a, b, atol=2 / 32768))
            for v in {v for g in GENDER_VOICES.values() for v in g.values()} & set(self.appkit.ids):
                self.appkit("Test.", v)   # the first sentence of a voice takes 0.2-0.9 s more
            log.info("Speech synthesis in-process (system voice: %s)", "AppKit" if self.appkit_system else "say")
        except Exception as e:   # no pyobjc, not macOS: `say` per sentence (~1 s slower)
            log.warning("In-process speech synthesis unavailable (%s), using say", e)

    def voice(self, lang: str) -> str | None:
        """Voice name for `say -v`, or None for the system voice."""
        v = self.overrides.get(lang) or self.overrides.get("*")
        if not v:
            g = GENDER_VOICES.get(self.gender, {}).get(lang)
            if g == "system" or g in {n for n, _ in self.voices}:
                v = g
        if v == "system":
            return None
        return v or pick_voice(lang, self.voices, self.REGION.get(lang, ""))

    def _synth(self, text: str, voice: str | None) -> tuple[np.ndarray, int]:
        if self.appkit and (voice in self.appkit.ids or (voice is None and self.appkit_system)):
            try:
                return self.appkit(text, voice)
            except (OSError, ValueError) as e:
                log.warning("AppKit synthesis failed (%s), using say", e)
        return say_synth(text, voice)

    async def synth(self, text: str, lang: str) -> tuple[np.ndarray, int, str | None]:
        """(audio, sample rate, voice name) of `text`, not played yet."""
        voice = self.voice(lang)
        audio, sr = await asyncio.wrap_future(self.thread.submit(self._synth, text, voice))
        return audio, sr, voice

    async def play(self, audio: np.ndarray, sr: int, on_start=None) -> float:
        """Plays on the robot speaker, one sound at a time; on_start(duration) right before playback."""
        async with self.lock:
            dev = find_output_device(self.device_name)   # looked up each time (after a re-init the index can change)
            if dev is None:
                log.warning("No output device matching %r, using the default speaker", self.device_name)
            dur = len(audio) / sr
            if on_start:
                on_start(dur)
            try:
                await asyncio.to_thread(lambda: (sd.play(audio, sr, device=dev), sd.wait()))
            except Exception as e:   # stale device after re-plugging: let MicSource re-initialize, then retry once
                from .audio import RESTART

                log.warning("Playback failed (%s), re-initializing audio and retrying", e)
                RESTART.set()
                await asyncio.sleep(3)
                dev = find_output_device(self.device_name)
                await asyncio.to_thread(lambda: (sd.play(audio, sr, device=dev), sd.wait()))
            return dur

    async def speak(self, text: str, lang: str, on_start=None) -> float:
        """Say `text` on the robot speaker; returns the duration in s. on_start(duration) right before playback."""
        audio, sr, voice = await self.synth(text, lang)
        log.info("Speaking (%s, %s, %.1f s): %s", lang, voice or "default voice", len(audio) / sr, text)
        return await self.play(audio, sr, on_start)
