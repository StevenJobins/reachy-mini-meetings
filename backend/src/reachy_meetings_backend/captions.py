"""Live captions for the speech bubbles: room audio -> Whisper -> translation -> WebSocket.

    reachy-captions                              # Mac mic / Reachy mic, captions on ws://0.0.0.0:8766
    reachy-captions --lang de --target en         # fixed spoken language: ~1 s faster per utterance
    reachy-captions --translator none            # captions only, no DeepL key needed
    reachy-captions --file meeting.wav           # test without a room

Protocol (server -> headset, one JSON per message; keep in sync with xr-client):
  hello    {"version": str, "target": str}
  caption  {"id": int, "final": bool, "text": str, "lang": str, "translation": str | null,
            "target": str, "azimuth_deg": float | null, "t_start": s, "t_end": s}

The same `id` is sent several times: partials (final=false) while the person talks, then the
final text, then once more with the translation. Clients upsert by `id`. A final with empty
text means "nothing was said" -> remove the bubble. Times are wall clock (time.time()).
azimuth_deg: speaker direction in the robot base frame, + = left, 0 = robot forward.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import websockets

from .segmenter import Segment, Segmenter

log = logging.getLogger("reachy_captions")

PROTOCOL_VERSION = "0.1"


class CaptionServer:
    def __init__(self, host: str, port: int, target: str) -> None:
        self.host, self.port, self.target = host, port, target
        self.clients: set = set()
        self.recent: deque[str] = deque(maxlen=20)  # last finals, replayed to new clients

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
            await ws.wait_closed()
        finally:
            self.clients.discard(ws)

    def send(self, caption: dict) -> None:
        msg = json.dumps({"type": "caption", **caption})
        if caption["final"]:
            self.recent.append(msg)
        websockets.broadcast(self.clients, msg)


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
        self.server = CaptionServer(args.host, args.port, args.target)
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
        if args.robot:
            from .doa import DoaTracker

            self.doa = DoaTracker(args.robot)

    async def _stt(self, model, audio) -> tuple[str, str]:
        self.pending += 1
        try:
            return await asyncio.get_running_loop().run_in_executor(self.worker, model, audio)
        finally:
            self.pending -= 1

    def _caption(self, seg: Segment, wall_end: float, text: str, lang: str,
                 translation: str | None = None) -> dict:
        t_start = wall_end - (seg.t_end - seg.t_start)
        return {
            "id": seg.id, "final": seg.final, "text": text, "lang": lang,
            "translation": translation, "target": self.args.target,
            "azimuth_deg": self.doa.azimuth(t_start, wall_end) if self.doa else None,
            "t_start": round(t_start, 3), "t_end": round(wall_end, 3),
        }

    async def _partial(self, seg: Segment, wall_end: float) -> None:
        text, lang = await self._stt(self.stt_partial, seg.audio)
        if text and seg.id not in self.finalized:
            self.server.send(self._caption(seg, wall_end, text, lang))

    async def _final(self, seg: Segment, wall_end: float) -> None:
        text, lang = await self._stt(self.stt, seg.audio)
        self.finalized.add(seg.id)
        cap = self._caption(seg, wall_end, text, lang)
        self.server.send(cap)
        log.info("[%d %s] %s", seg.id, lang, text)
        if not text or lang == self.args.target or not self.translator:
            return
        translation = await self.translator(text)
        if translation:
            log.info("[%d %s] %s", seg.id, self.args.target, translation)
            self.server.send({**cap, "translation": translation})

    async def run(self) -> None:
        from .audio import FileSource, MicSource

        source = FileSource(self.args.file) if self.args.file else MicSource(self.args.mic)
        chunks: asyncio.Queue = asyncio.Queue()
        tasks = [asyncio.create_task(self.server.run())]
        if self.doa:
            tasks.append(asyncio.create_task(self.doa.run()))
        src = asyncio.create_task(source.run(chunks))
        jobs: set[asyncio.Task] = set()

        def spawn(coro):
            t = asyncio.create_task(coro)
            jobs.add(t)
            t.add_done_callback(jobs.discard)

        while not (src.done() and chunks.empty()):
            try:
                chunk = await asyncio.wait_for(chunks.get(), 0.5)
            except asyncio.TimeoutError:
                continue
            for seg in self.segmenter.push(chunk):
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
    ap.add_argument("--robot", default="ws://localhost:8765",
                    help="robot bridge for speaker direction (DoA); '' to disable")
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
    for noisy in ("websockets", "faster_whisper", "httpx", "httpx2", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        asyncio.run(Pipeline(args).run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    cli()
