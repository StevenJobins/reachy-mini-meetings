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
  summary  {"summary": [str], "actions": [{"who": str, "what": str}], "t": s}
  vad      {"speaking": bool, "t": s}   instantly from the neural VAD (~0.1 s), long before any text:
           sent when speech starts/ends and every 0.25 s while it lasts (speaker following uses it)
  binary   the room audio itself: int16 little-endian PCM, 16 kHz mono, ~40 ms per frame. The robot's own
           WebRTC audio drops ~55 % of the sound (daemon bug, 0 packets lost); the page plays this instead.
           Between utterances it is turned down by --pause-db (noise gate driven by the neural VAD).

The same `id` is sent several times: partials (final=false) while the person talks, then the
final text, then once more with the translation. Clients upsert by `id`. A final with empty
text means "nothing was said" -> remove the bubble. Times are wall clock (time.time()).
Speaker direction (+ = left): doa_deg relative to the head/camera (to pick the face in the video),
azimuth_deg in the robot base frame. A new client gets the latest summary right after hello.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import websockets

from .segmenter import Segment, Segmenter

log = logging.getLogger("reachy_captions")

PROTOCOL_VERSION = "0.2"


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


class CaptionServer:
    def __init__(self, host: str, port: int, target: str) -> None:
        self.host, self.port, self.target = host, port, target
        self.clients: set = set()
        self.recent: deque[str] = deque(maxlen=20)  # last finals, replayed to new clients
        self.summary: str | None = None

    async def run(self) -> None:
        async with websockets.serve(self._handle, self.host, self.port):
            log.info("Captions on ws://%s:%d", self.host, self.port)
            await asyncio.Future()

    async def _handle(self, ws) -> None:
        self.clients.add(ws)
        try:
            await ws.send(json.dumps({"type": "hello", "version": PROTOCOL_VERSION,
                                      "target": self.target}))
            for msg in list(self.recent):
                await ws.send(msg)
            if self.summary:
                await ws.send(self.summary)
            await ws.wait_closed()
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
        self.worker = ThreadPoolExecutor(1)  # Whisper runs one job at a time
        self.pending = 0
        self.finalized: set[int] = set()
        self.partial_tr: dict[int, tuple[float, int]] = {}   # id -> (time, text length) of last partial translation
        # Language per utterance, detected by the fast partial model, so the big model needn't detect it
        # again (that costs ~1 s): auto language at no extra delay.
        self.seg_lang: dict[int, str] = {}
        self.server = CaptionServer(args.host, args.port, args.target)
        try:
            from .vad import SileroVad

            self.segmenter = Segmenter(vad=SileroVad())
        except (ImportError, OSError) as e:  # onnxruntime missing / no download: loudness fallback
            log.warning("Silero VAD unavailable (%s), using the loudness threshold", e)
            self.segmenter = Segmenter()
        self.translator = None
        try:
            if args.translator == "deepl":
                from .translate import DeepLTranslator

                self.translator = DeepLTranslator(args.target)
            elif args.translator == "claude":
                from .translate import ClaudeTranslator

                self.translator = ClaudeTranslator(args.target, args.claude_model)
        except (RuntimeError, ImportError) as e:
            log.warning("No translation: %s", e)
        self.doa = None
        if args.daemon:
            from .doa import DoaTracker

            self.doa = DoaTracker(args.daemon)
        self.gate = NoiseGate(args.pause_db) if args.pause_db < 0 else None
        self.summarizer = None
        if args.summary == "gemini":
            from .summary import Summarizer

            try:
                self.summarizer = Summarizer(args.target, args.summary_model, args.summary_every)
            except RuntimeError as e:
                log.warning("No summary: %s", e)

    async def _stt(self, model, audio, language=None) -> tuple[str, str]:
        self.pending += 1
        try:
            return await asyncio.get_running_loop().run_in_executor(self.worker, model, audio, language)
        finally:
            self.pending -= 1

    def _caption(self, seg: Segment, wall_end: float, text: str, lang: str,
                 translation: str | None = None) -> dict:
        t_start = wall_end - (seg.t_end - seg.t_start)
        doa_deg, azimuth_deg = self.doa.direction(t_start, wall_end) if self.doa else (None, None)
        return {
            "id": seg.id, "final": seg.final, "text": text, "lang": lang,
            "translation": translation, "target": self.args.target,
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
        if (self.translator and lang != self.args.target and len(text) - last_len >= 15
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
        if text and lang != self.args.target and self.translator:
            translation = await self.translator(text)
            if translation:
                log.info("[%d %s] %s", seg.id, self.args.target, translation)
                cap = {**cap, "translation": translation}
                self.server.send(cap)
        if self.summarizer:
            self.summarizer.add(cap)

    async def run(self) -> None:
        from .audio import FileSource, MicSource

        source = FileSource(self.args.file) if self.args.file else MicSource(self.args.mic)
        chunks: asyncio.Queue = asyncio.Queue()
        tasks = [asyncio.create_task(self.server.run())]
        if self.doa:
            tasks.append(asyncio.create_task(self.doa.run()))
        if self.summarizer:
            tasks.append(asyncio.create_task(self.summarizer.run(self.server.send_summary)))
        src = asyncio.create_task(source.run(chunks))
        jobs: set[asyncio.Task] = set()

        def spawn(coro):
            t = asyncio.create_task(coro)
            jobs.add(t)
            t.add_done_callback(jobs.discard)

        vad_on, vad_sent = False, 0.0
        while not (src.done() and chunks.empty()):
            try:
                chunk = await asyncio.wait_for(chunks.get(), 0.5)
            except asyncio.TimeoutError:
                continue
            segs = self.segmenter.push(chunk)
            speaking = self.segmenter.active or self.segmenter.prob > 0.4
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
                    help="live summary + action items; gemini needs GEMINI_API_KEY (free tier)")
    ap.add_argument("--summary-model", default="gemini-flash-lite-latest",
                    help="has free quota for new accounts (gemini-flash-latest ran into 429)")
    ap.add_argument("--summary-every", type=float, default=60, help="seconds between summaries")
    ap.add_argument("--pause-db", type=float, default=-14,
                    help="room sound level between utterances (noise gate, needs the neural VAD); 0 = off")
    ap.add_argument("--no-focus-mic", dest="focus_mic", action="store_false",
                    help="leave the robot's mic array as it is (default: stronger noise suppression, "
                         "restored on exit)")
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
