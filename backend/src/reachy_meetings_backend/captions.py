"""Live captions for the speech bubbles: room audio -> Whisper -> translation -> WebSocket.

    reachy-captions                              # Mac mic / Reachy mic, captions on ws://0.0.0.0:8766
    reachy-captions --target en                  # any spoken language (detected per utterance)
    reachy-captions --lang de --target en         # force one spoken language
    reachy-captions --translator none            # captions only, no DeepL key needed
    reachy-captions --file meeting.wav           # test without a room

Protocol (server -> headset, one JSON per message; keep in sync with xr-client):
  hello    {"version": str, "target": str}
  caption  {"id": int, "final": bool, "text": str, "lang": str, "translation": str | null,
            "target": str, "doa_deg": float | null, "azimuth_deg": float | null,
            "t_start": s, "t_end": s}
  summary  {"summary": [str], "actions": [{"who": str, "what": str, "when": str}], "next_steps": [str], "t": s}
  faces    {"people": [{"cx", "cy", "top", "w", "h", "mouth"}], "t": s}   faces in the robot camera found here
           (vision.py, ~20/s): the page uses them instead of its own, much slower detector
  me       {"text": str, "lang": str, "translation": str | null, "target": str, "t": s}   what the remote
           user said (their headset mic, see below) and what Reachy said for them in the meeting language
  vad      {"speaking": bool, "t": s}   instantly from the neural VAD (~0.1 s), long before any text:
           sent when speech starts/ends and every 0.25 s while it lasts (speaker following uses it)
  binary   the room audio itself: int16 little-endian PCM, 16 kHz mono, ~40 ms per frame. The robot's own
           WebRTC audio drops ~55 % of the sound (daemon bug, 0 packets lost); the page plays this instead.
           Between utterances it is turned down by --pause-db (noise gate driven by the neural VAD).

Client -> server: {"type": "auth", "hf_token": str} first (checked through the tunnel only);
{"type": "voice", "gender": "male" | "female"} picks Reachy's voice (voice.py GENDER_VOICES, per language);
{"type": "lang", "meeting": "auto" | "de" | "en" | ..., "target": "en" | ...} (both optional, ISO 639-1):
the language Reachy speaks for the remote user ("auto" = --meeting-lang, else the one most spoken in the
room lately) and the bubble language (caption translations, notes; default --target); sent by the page on
every (re)connect, applies to everyone connected;
{"type": "log", "lines": [str]} appends the page's log to ~/Library/Logs/reachy-headset.log; binary frames
(int16 PCM, 16 kHz mono) = the remote user's voice while "translate me" is on: transcribed (language
detected, never forced by --lang), translated into the meeting language and spoken by Reachy, sentences in
order; {"type": "voice_end"} = the page stopped sending them (translate off, muted): the sentence ends now
(also after 1 s without frames).

The same `id` is sent several times: partials (final=false) while the person talks, then the
final text, then once more with the translation. Clients upsert by `id`. A final with empty
text means "nothing was said" -> remove the bubble. Times are wall clock (time.time()).
Speaker direction (+ = left): doa_deg relative to the head/camera (to pick the face in the video),
azimuth_deg in the robot base frame. A new client gets the latest summary right after hello.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import logging
import os
import queue
import re
import signal
import subprocess
import threading
import time
import urllib.request
from collections import deque
from concurrent.futures import Future
from pathlib import Path

import numpy as np
import websockets

from .segmenter import Segment, Segmenter, SegmenterCfg
from .stt import AUTO

log = logging.getLogger("reachy_captions")

PROTOCOL_VERSION = "0.2"
HEADSET_LOG = Path.home() / "Library/Logs/reachy-headset.log"   # the page's log lines ({"type": "log"})


class NoiseGate:
    """Turns the room sound down while nobody speaks (the background hum stays out of the headset), back up
    within one frame when speech starts. Gain ramps across each chunk, so there are no clicks."""

    def __init__(self, pause_db: float, hold_s: float = 0.35, release_s: float = 0.25) -> None:
        self.floor = 10 ** (pause_db / 20)
        self.hold_s, self.release_s = hold_s, release_s
        self.gain = self.floor
        self.last_speech = -1e9

    def __call__(self, chunk: np.ndarray, speaking: bool, now: float) -> np.ndarray:
        if speaking:
            self.last_speech = now
            target = 1.0
        else:
            quiet = now - self.last_speech - self.hold_s   # keep the tail of a sentence
            target = 1.0 if quiet < 0 else max(self.floor, 1.0 - (1.0 - self.floor) * quiet / self.release_s)
        ramp = np.linspace(self.gain, target, len(chunk), dtype=np.float32)
        self.gain = target
        return chunk * ramp


def hf_username(token: str) -> str | None:
    """Hugging Face account of a sign-in token (the page's OAuth token), or None if invalid."""
    for url, key in (("https://huggingface.co/oauth/userinfo", "preferred_username"),
                     ("https://huggingface.co/api/whoami-v2", "name")):
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.load(r).get(key)
        except (OSError, ValueError):
            continue
    return None


LANG_RE = re.compile(r"[a-z]{2}")


def parse_lang(msg: dict) -> tuple[str | None, str | None]:
    """{"type": "lang", "meeting": "auto" | "de" | ..., "target": "en" | ...} -> (meeting, target); None = not
    given or invalid (ISO 639-1 codes only)."""
    meeting, target = msg.get("meeting"), msg.get("target")
    meeting = meeting.lower() if isinstance(meeting, str) else None
    target = target.lower() if isinstance(target, str) else None
    return (meeting if meeting == "auto" or (meeting and LANG_RE.fullmatch(meeting)) else None,
            target if target and LANG_RE.fullmatch(target) else None)


class Worker:
    """One thread for all Whisper jobs (the models are not thread-safe); a lower `prio` runs first, so the
    remote user's sentence doesn't wait behind queued room captions (measured: up to 2.5 s in a busy room)."""

    def __init__(self) -> None:
        self.jobs: queue.PriorityQueue = queue.PriorityQueue()
        self.seq = itertools.count()   # FIFO within one priority
        threading.Thread(target=self._run, daemon=True).start()

    def submit(self, fn, prio: int = 1) -> Future:
        fut: Future = Future()
        self.jobs.put((prio, next(self.seq), fn, fut))
        return fut

    def _run(self) -> None:
        while True:
            _, _, fn, fut = self.jobs.get()
            if not fut.set_running_or_notify_cancel():   # cancelled while waiting
                continue
            try:
                fut.set_result(fn())
            except BaseException as e:
                fut.set_exception(e)


class CaptionServer:
    def __init__(self, host: str, port: int, target: str, allow_hf: set[str] | None = None) -> None:
        self.host, self.port, self.target = host, port, target
        # Clients through the tunnel (Cloudflare adds a cf-ray header) must first send
        # {"type": "auth", "hf_token": ...} of one of these accounts. Local / LAN clients need nothing.
        self.allow_hf = {u.lower() for u in allow_hf or ()}
        self.token_users: dict[str, str | None] = {}
        self.clients: set = set()
        self.on_voice = None   # binary frames from a client: the remote user's voice
        self.on_message = None   # JSON from a client (e.g. {"type": "voice", "gender": "female"})
        self.recent: deque[str] = deque(maxlen=20)  # last finals, replayed to new clients
        self.summary: str | None = None

    async def run(self) -> None:
        async with websockets.serve(self._handle, self.host, self.port):
            log.info("Captions on ws://%s:%d", self.host, self.port)
            await asyncio.Future()

    async def _authorized(self, ws) -> bool:
        if "cf-ray" not in ws.request.headers:
            return True
        try:
            msg = json.loads(await asyncio.wait_for(ws.recv(), 10))
            token = msg.get("hf_token") if msg.get("type") == "auth" else None
        except (asyncio.TimeoutError, ValueError, AttributeError, websockets.ConnectionClosed):
            token = None
        if token and token not in self.token_users:
            self.token_users[token] = await asyncio.to_thread(hf_username, token)
        user = self.token_users.get(token) if token else None
        if user and user.lower() in self.allow_hf:
            log.info("Tunnel client signed in as %s", user)
            return True
        log.warning("Tunnel client rejected (HF account %s)", user or "none")
        await ws.close(4001, "sign in with an allowed Hugging Face account")
        return False

    async def _handle(self, ws) -> None:
        if not await self._authorized(ws):
            return
        self.clients.add(ws)
        try:
            await ws.send(json.dumps({"type": "hello", "version": PROTOCOL_VERSION,
                                      "target": self.target}))
            for msg in list(self.recent):
                await ws.send(msg)
            if self.summary:
                await ws.send(self.summary)
            async for msg in ws:
                if isinstance(msg, bytes):
                    if self.on_voice:
                        self.on_voice(msg)
                elif self.on_message:
                    try:
                        self.on_message(json.loads(msg))
                    except ValueError:
                        pass
        finally:
            self.clients.discard(ws)

    def send(self, caption: dict) -> None:
        msg = json.dumps({"type": "caption", **caption})
        if caption["final"]:
            self.recent.append(msg)
        websockets.broadcast(self.clients, msg)

    def send_audio(self, chunk) -> None:
        if self.clients:
            pcm = (np.clip(chunk, -1, 1) * 32767).astype("<i2").tobytes()
            websockets.broadcast(self.clients, pcm)

    def send_vad(self, speaking: bool) -> None:
        websockets.broadcast(self.clients, json.dumps({"type": "vad", "speaking": speaking,
                                                       "t": round(time.time(), 3)}))

    def send_faces(self, people: list, t: float) -> None:
        if self.clients:
            websockets.broadcast(self.clients, json.dumps({"type": "faces", "people": people, "t": round(t, 3)}))

    def send_me(self, me: dict) -> None:
        websockets.broadcast(self.clients, json.dumps({"type": "me", **me}))

    def send_summary(self, notes: dict) -> None:
        self.summary = json.dumps({"type": "summary", **notes})
        websockets.broadcast(self.clients, self.summary)


class Pipeline:
    def __init__(self, args) -> None:
        from .stt import Transcriber

        self.args = args
        self.stt = Transcriber(args.model, args.lang, args.engine)
        self.stt_partial = (Transcriber(args.partial_model, args.lang, args.engine)
                            if args.partial_model != "none" else None)
        self.worker = Worker()  # Whisper runs one job at a time
        self.pending = 0
        self.finalized: set[int] = set()
        self.partial_tr: dict[int, tuple[float, int]] = {}   # id -> (time, text length) of last partial translation
        # Language per utterance, detected by the fast partial model, so the big model needn't detect it
        # again (that costs ~1 s): auto language at no extra delay.
        self.seg_lang: dict[int, str] = {}
        self.server = CaptionServer(args.host, args.port, args.target, args.allow_hf)
        try:
            from .vad import SileroVad

            self.segmenter = Segmenter(vad=SileroVad())
        except (ImportError, OSError) as e:  # onnxruntime missing / no download: loudness fallback
            log.warning("Silero VAD unavailable (%s), using the loudness threshold", e)
            self.segmenter = Segmenter()
        # Languages, switchable from the page ({"type": "lang"}): the bubbles' language, and the one Reachy
        # speaks for the remote user ("auto" = --meeting-lang, else the language most spoken in the room)
        self.target = args.target
        self.meeting_choice = "auto"
        self.translator = self._new_translator(self.target)   # room captions, with context
        self.doa = None
        if args.daemon:
            from .doa import DoaTracker

            self.doa = DoaTracker(args.daemon)
        self.gate = NoiseGate(args.pause_db) if args.pause_db < 0 else None
        # The remote user's voice -> meeting language -> Reachy's speaker (voice.py)
        self.voice = None
        self.me_segmenter = None
        self.me_queue: asyncio.Queue = asyncio.Queue()   # voice frames, None = the user stopped sending
        self.me_out: asyncio.Queue = asyncio.Queue()     # (prepare task, speech end) in speaking order
        self.me_translators: dict = {}
        self.room_langs: deque[tuple[float, str]] = deque(maxlen=50)   # (time, lang) of room utterances
        self.speaking_until = 0.0   # Reachy talks: its own voice must not become captions or turn the head
        if args.voice_out != "none":
            from .voice import VoiceOut

            try:
                self.voice = VoiceOut(args.voice_out, args.voice)
                me_cfg = SegmenterCfg(silence_s=args.me_silence, early_s=args.me_early)
                try:
                    from .vad import SileroVad

                    self.me_segmenter = Segmenter(me_cfg, vad=SileroVad())   # own VAD state for the second stream
                except (ImportError, OSError):
                    self.me_segmenter = Segmenter(me_cfg)
                self.server.on_voice = self._me_voice
            except (OSError, subprocess.CalledProcessError) as e:
                log.warning("No voice output: %s", e)
        self.server.on_message = self._client_message
        self.summarizer = None
        if args.summary == "gemini":
            from .summary import Summarizer

            try:
                self.summarizer = Summarizer(args.target, args.summary_model, args.summary_every)
            except RuntimeError as e:
                log.warning("No summary: %s", e)

    async def _stt(self, model, audio, language=None, timing: dict | None = None, prio: int = 1) -> tuple[str, str]:
        self.pending += 1
        queued = time.time()

        def job():
            if timing is not None:
                timing["wait"] = time.time() - queued   # time spent behind other Whisper jobs
            return model(audio, language)
        try:
            return await asyncio.wrap_future(self.worker.submit(job, prio))
        finally:
            self.pending -= 1

    def _caption(self, seg: Segment, wall_end: float, text: str, lang: str,
                 translation: str | None = None) -> dict:
        t_start = wall_end - (seg.t_end - seg.t_start)
        doa_deg, azimuth_deg = self.doa.direction(t_start, wall_end) if self.doa else (None, None)
        return {
            "id": seg.id, "final": seg.final, "text": text, "lang": lang,
            "translation": translation, "target": self.target,
            "doa_deg": doa_deg, "azimuth_deg": azimuth_deg,
            "t_start": round(t_start, 3), "t_end": round(wall_end, 3),
        }

    async def _partial(self, seg: Segment, wall_end: float) -> None:
        text, lang = await self._stt(self.stt_partial, seg.audio)
        if not text or seg.id in self.finalized:
            return
        self.seg_lang[seg.id] = lang
        cap = self._caption(seg, wall_end, text, lang)
        self.server.send(cap)
        # Translate live text too, so the bubble is readable while the person still talks. Throttled
        # (DeepL quota): only when enough new text came in since the last partial translation.
        last_t, last_len = self.partial_tr.get(seg.id, (0.0, 0))
        now = time.time()
        if (self.translator and lang != self.target and len(text) - last_len >= 15
                and now - last_t >= 1.2):
            self.partial_tr[seg.id] = (now, len(text))
            translation = await self.translator(text, remember=False)
            if translation and seg.id not in self.finalized:
                self.server.send({**cap, "translation": translation})

    async def _final(self, seg: Segment, wall_end: float) -> None:
        text, lang = await self._stt(self.stt, seg.audio, self.seg_lang.pop(seg.id, None))
        self.finalized.add(seg.id)
        self.partial_tr.pop(seg.id, None)
        cap = self._caption(seg, wall_end, text, lang)
        self.server.send(cap)
        log.info("[%d %s] %s", seg.id, lang, text)
        if text:
            self.room_langs.append((time.time(), lang))
        target, translator = self.target, self.translator
        if text and lang != target and translator:
            translation = await translator(text)
            if translation and target == self.target:   # not if the bubble language changed meanwhile
                log.info("[%d %s] %s", seg.id, target, translation)
                cap = {**cap, "translation": translation}
                self.server.send(cap)
        if self.summarizer:
            self.summarizer.add(cap)

    def _client_message(self, msg: dict) -> None:
        if msg.get("type") == "log" and isinstance(msg.get("lines"), list):
            with HEADSET_LOG.open("a") as f:
                f.writelines(f"{line}\n" for line in msg["lines"] if isinstance(line, str))
            return
        if msg.get("type") == "voice" and msg.get("gender") in ("male", "female") and self.voice:
            if self.voice.gender != msg["gender"]:
                self.voice.gender = msg["gender"]
                log.info("Reachy's voice: %s (%s)", msg["gender"], self.voice.voice(self.meeting_lang()) or "system voice")
        if msg.get("type") == "voice_end" and self.voice:   # the page stopped sending the remote user's voice
            self.me_queue.put_nowait(None)
        if msg.get("type") == "lang":
            meeting, target = parse_lang(msg)
            if meeting and meeting != self.meeting_choice:
                self.meeting_choice = meeting
                log.info("Reachy speaks for the remote user: %s (now %s)", meeting, self.meeting_lang())
            if target and target != self.target:
                self.target = self.server.target = target
                self.translator = self._new_translator(target)
                if self.summarizer:
                    self.summarizer.set_target(target)
                log.info("Bubbles in: %s", target)

    def meeting_lang(self) -> str:
        """The page's choice, else --meeting-lang, else the language most spoken in the room in the last 10 min."""
        if self.meeting_choice != "auto":
            return self.meeting_choice
        if self.args.meeting_lang:
            return self.args.meeting_lang
        now = time.time()
        langs = [lang for t, lang in self.room_langs if now - t < 600]
        return max(set(langs), key=langs.count) if langs else (self.args.lang or "de")

    def _new_translator(self, target: str):
        try:
            if self.args.translator == "deepl":
                from .translate import DeepLTranslator

                return DeepLTranslator(target)
            if self.args.translator == "claude":
                from .translate import ClaudeTranslator

                return ClaudeTranslator(target, self.args.claude_model)
        except (RuntimeError, ImportError) as e:
            log.warning("No translation into %s: %s", target, e)
        return None

    def _translator_to(self, target: str):
        """Translator for the remote user's voice (no shared context with the room), one per language."""
        if target not in self.me_translators:
            self.me_translators[target] = self._new_translator(target)
        return self.me_translators[target]

    def _me_stt(self, audio: np.ndarray, _language=None) -> tuple[str, str]:
        """The remote user's speech, in whatever language they speak (never --lang). The fast model finds the
        language (one encoder pass, ~0.1 s), so the big one needn't detect it again (~0.6 s)."""
        lang = self.stt_partial.detect_language(audio) if self.stt_partial else AUTO
        return self.stt(audio, lang)

    async def _me_prepare(self, seg: Segment) -> dict | None:
        """Transcribe, translate and synthesize one sentence of the remote user; played later, in order."""
        t0, timing = time.time(), {}
        text, lang = await self._stt(self._me_stt, seg.audio, timing=timing, prio=0)
        t_stt = time.time()
        if not text:
            return None
        target = self.meeting_lang()
        tr = self._translator_to(target) if lang != target else None
        translation = await tr(text, remember=False) if tr else None
        t_tr = time.time()
        say, out_lang = (translation, target) if translation else (text, lang)
        audio, sr, voice = await self.voice.synth(say, out_lang)
        return {"text": text, "lang": lang, "translation": translation, "target": target, "say": say,
                "out_lang": out_lang, "audio": audio, "sr": sr, "voice": voice, "audio_s": len(seg.audio) / 16000,
                "times": (t0, t_stt, t_tr, time.time(), timing.get("wait", 0.0))}

    async def _me_play(self, r: dict, t_end: float) -> None:
        log.info("[me %s->%s] %s -> %s", r["lang"], r["out_lang"], r["text"], r["say"])
        self.server.send_me({k: r[k] for k in ("text", "lang", "translation", "target")} | {"t": round(time.time(), 3)})
        t0, t_stt, t_tr, t_syn, wait = r["times"]

        def started(dur: float) -> None:
            now = time.time()
            self.speaking_until = now + dur + 0.4   # + room echo
            log.info("me timing (s): start %.2f  stt %.2f (wait %.2f)  translate %.2f  synth %.2f  "
                     "-> playback %.2f after speech end (%.1f s audio, %s, %.1f s)", t0 - t_end, t_stt - t0, wait,
                     t_tr - t_stt, t_syn - t_tr, now - t_end, r["audio_s"], r["voice"] or "system voice", dur)
        await self.voice.play(r["audio"], r["sr"], on_start=started)

    async def _me_player(self) -> None:
        """Speaks the prepared sentences strictly in the order they were said."""
        while True:
            task, t_end = await self.me_out.get()
            try:
                r = await task
                if r:
                    await self._me_play(r, t_end)
            except asyncio.CancelledError:
                if not task.cancelled():
                    raise
            except Exception:
                log.exception("Translate me: sentence failed")

    async def _me_loop(self) -> None:
        """The remote user's voice -> sentences. Work on a sentence starts at the tentative end (early_s of
        silence) and is kept if the sentence really ends there (silence_s), so Whisper, DeepL and the synthesis
        mostly run while we still wait for the end of the sentence."""
        cfg = self.me_segmenter.cfg
        prepared: dict[int, tuple[int, asyncio.Task, float]] = {}   # seg id -> (samples, task, speech end)
        while True:
            timeout = False
            try:
                chunk = await asyncio.wait_for(self.me_queue.get(), 1.0)
            except asyncio.TimeoutError:
                chunk, timeout = None, True
            now = time.time()
            if chunk is None:   # no voice for 1 s (translate off, muted, connection lost) or voice_end
                seg = self.me_segmenter.flush()
                segs, silence = ([seg] if seg else []), (1.0 if timeout else 0.0)
            else:
                segs, silence = self.me_segmenter.push(chunk), cfg.silence_s
            for seg in segs:
                if seg.early:
                    old = prepared.pop(seg.id, None)
                    if old:
                        old[1].cancel()
                    prepared[seg.id] = (len(seg.audio), asyncio.create_task(self._me_prepare(seg)), now - cfg.early_s)
                elif seg.final:
                    n, task, t_end = prepared.pop(seg.id, (0, None, 0.0))
                    if n != len(seg.audio):   # speech went on after the tentative end: start over
                        if task:
                            task.cancel()
                        task, t_end = asyncio.create_task(self._me_prepare(seg)), now - silence
                    self.me_out.put_nowait((task, t_end))
                    for sid in [i for i in prepared if i < seg.id]:   # tentative ends of dropped blips
                        prepared.pop(sid)[1].cancel()

    def _me_voice(self, frame: bytes | None) -> None:
        self.me_queue.put_nowait(None if frame is None else np.frombuffer(frame, "<i2").astype(np.float32) / 32768)

    async def run(self) -> None:
        from .audio import FileSource, MicSource

        source = FileSource(self.args.file) if self.args.file else MicSource(self.args.mic)
        chunks: asyncio.Queue = asyncio.Queue()
        tasks = [asyncio.create_task(self.server.run())]
        if self.doa:
            tasks.append(asyncio.create_task(self.doa.run()))
        if self.summarizer:
            tasks.append(asyncio.create_task(self.summarizer.run(self.server.send_summary)))
        if self.voice:
            tasks += [asyncio.create_task(self._me_loop()), asyncio.create_task(self._me_player())]
        if self.args.vision_camera != "none" and not self.args.file:
            from .vision import FaceStream

            loop = asyncio.get_running_loop()
            FaceStream(self.args.vision_camera, self.args.vision_hz).start(
                lambda people, t: loop.call_soon_threadsafe(self.server.send_faces, people, t))
        if self.args.tunnel:
            from . import tunnel

            tasks.append(asyncio.create_task(tunnel.run(self.args.port)))
        src = asyncio.create_task(source.run(chunks))
        jobs: set[asyncio.Task] = set()

        def spawn(coro):
            t = asyncio.create_task(coro)
            jobs.add(t)
            t.add_done_callback(jobs.discard)

        vad_on, vad_sent = False, 0.0
        lag_logged = 0.0
        while not (src.done() and chunks.empty()):
            try:
                chunk = await asyncio.wait_for(chunks.get(), 0.5)
            except asyncio.TimeoutError:
                continue
            if chunks.qsize() > 25 and time.time() - lag_logged > 5:   # > 1 s of audio waiting
                lag_logged = time.time()
                log.warning("Audio processing %.1f s behind", chunks.qsize() * len(chunk) / 16000)
            if time.time() < self.speaking_until:   # Reachy is talking: its own voice is no room speech
                segs = self.segmenter.push(np.zeros_like(chunk))
            else:
                segs = self.segmenter.push(chunk)
            speaking = self.segmenter.active or self.segmenter.prob > 0.4
            if time.time() < self.speaking_until:
                # Reachy speaks the remote user's words: keep its voice out of the stream to the headset, or
                # the headset mic (Translate me) could pick it up again and translate it in a loop
                self.server.send_audio(np.zeros_like(chunk))
            else:
                self.server.send_audio(self.gate(chunk, speaking, time.time()) if self.gate else chunk)
            now = time.time()
            if self.segmenter.active != vad_on or (vad_on and now - vad_sent > 0.25):
                vad_on, vad_sent = self.segmenter.active, now
                self.server.send_vad(vad_on)
            for seg in segs:
                if seg.final:
                    spawn(self._final(seg, time.time()))
                elif self.stt_partial and self.pending == 0:  # skip partials while Whisper is behind
                    spawn(self._partial(seg, time.time()))
        if jobs:
            await asyncio.gather(*jobs)
        src.result()
        for t in tasks:
            t.cancel()


def cli() -> None:
    ap = argparse.ArgumentParser(description="Reachy Mini Meetings – live captions")
    ap.add_argument("--mic", default="Reachy",
                    help="input device name (substring); falls back to the default mic")
    ap.add_argument("--file", help="WAV file instead of the mic (played in real time)")
    ap.add_argument("--model",
                    help="Whisper model for final text: tiny/base/small/medium/large-v3/large-v3-turbo"
                         " (default depends on the hardware, see README)")
    ap.add_argument("--partial-model",
                    help="faster model for the live text while someone talks; 'none' = finals only")
    ap.add_argument("--engine", choices=["mlx", "faster-whisper"],
                    help="Whisper engine; default: mlx on Apple Silicon, faster-whisper elsewhere")
    ap.add_argument("--lang", help="spoken language (ISO code); default: auto-detect per utterance")
    ap.add_argument("--target", default="en", help="bubble language (ISO code)")
    ap.add_argument("--translator", choices=["deepl", "claude", "none"], default="deepl",
                    help="deepl needs DEEPL_AUTH_KEY, claude needs ANTHROPIC_API_KEY (paid)")
    ap.add_argument("--claude-model", default="claude-opus-5-5")
    ap.add_argument("--daemon", default="http://localhost:8000",
                    help="Reachy Mini daemon for the speaker direction (DoA); '' to disable")
    ap.add_argument("--summary", choices=["gemini", "none"], default="gemini",
                    help="live summary, action items, next steps; gemini needs GEMINI_API_KEY (free tier)")
    ap.add_argument("--summary-model", default="gemini-flash-lite-latest",
                    help="has free quota for new accounts (gemini-flash-latest ran into 429)")
    ap.add_argument("--summary-every", type=float, default=60, help="seconds between summaries")
    ap.add_argument("--pause-db", type=float, default=-14,
                    help="room sound level between utterances (noise gate, needs the neural VAD); 0 = off")
    ap.add_argument("--no-focus-mic", dest="focus_mic", action="store_false",
                    help="leave the robot's mic array as it is (default: stronger noise suppression, "
                         "restored on exit)")
    ap.add_argument("--voice-out", default="Reachy",
                    help="speaker for the remote user's translated voice (device name substring); 'none' = off")
    ap.add_argument("--voice", default="", help="macOS voice per language, e.g. de=Markus,en=Ava; 'system' = "
                                                "the system voice (Siri), '*=system' for all languages "
                                                "(default: Viktor for German, else the best installed one)")
    ap.add_argument("--me-silence", type=float, default=0.8,
                    help="Translate me: silence (s) that ends a sentence of the remote user")
    ap.add_argument("--me-early", type=float, default=0.3,
                    help="Translate me: start transcribing/translating after this much silence, before the "
                         "sentence is known to be over (0 = off)")
    ap.add_argument("--meeting-lang", help="language Reachy speaks for the remote user (default: the one most "
                                           "spoken in the room lately)")
    ap.add_argument("--vision-camera", default="Reachy Mini Camera",
                    help="camera (name substring) for the face detection sent to the page; 'none' = off")
    ap.add_argument("--vision-hz", type=float, default=20, help="face detections per second sent to the page")
    ap.add_argument("--tunnel", action="store_true",
                    help="wireless headset: Cloudflare quick tunnel, address published for the page (tunnel.py)")
    ap.add_argument("--allow-hf", default="",
                    help="Hugging Face accounts allowed through the tunnel, comma-separated "
                         "(default: the account this Mac is signed in with)")
    ap.add_argument("--require-mic", action="store_true",
                    help="exit if the --mic device is missing instead of using the default mic (autostart)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    from .stt import default_engine, has_cuda

    args.engine = args.engine or default_engine()
    fast = args.engine == "mlx" or has_cuda()
    # CPU-only: large models and live partials are too slow
    args.model = args.model or ("large-v3-turbo" if fast else "small")
    args.partial_model = args.partial_model or ("small" if fast else "none")

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.require_mic and not args.file:
        from .audio import find_input_device

        if find_input_device(args.mic) is None:
            log.error("No input device matching %r", args.mic)
            raise SystemExit(1)
    args.allow_hf = {u.strip() for u in args.allow_hf.split(",") if u.strip()}
    if args.tunnel and not args.allow_hf:
        try:
            from huggingface_hub import whoami

            args.allow_hf = {whoami()["name"]}
        except Exception as e:   # not signed in / offline: nobody gets in through the tunnel
            log.warning("No HF account for the tunnel (%s); use --allow-hf", e)
        log.info("Tunnel open for HF accounts: %s", ", ".join(sorted(args.allow_hf)) or "nobody")
    for noisy in ("websockets", "faster_whisper", "httpx", "httpx2", "huggingface_hub", "deepl"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    signal.signal(signal.SIGTERM, signal.default_int_handler)   # pkill / kill = Ctrl-C: still restore the mic array
    restore_mic = None
    if args.focus_mic and not args.file:
        try:
            from .micarray import focus_on

            restore_mic = focus_on()
        except (ImportError, OSError, ValueError) as e:
            log.warning("Directional mic not set (%s)", e)
    try:
        asyncio.run(Pipeline(args).run())
    except KeyboardInterrupt:
        pass
    finally:
        if restore_mic:
            restore_mic()
        logging.shutdown()
        # don't wait for the Whisper worker / audio threads at interpreter exit: a stuck one kept old
        # instances alive (holding the mic) after kill
        os._exit(0)


if __name__ == "__main__":
    cli()
