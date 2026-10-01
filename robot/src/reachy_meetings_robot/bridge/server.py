"""WebSocket server: the robot's single entry point for the xr-client and the backend.

Runs its own asyncio loop in a background thread so the rest of the robot code stays synchronous.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable

import websockets

from ..config import BridgeCfg
from ..state import StateStore
from .protocol import (
    PROTOCOL_VERSION,
    GestureMsg,
    HeadPoseMsg,
    Incoming,
    ProtocolError,
    SayMsg,
    StopSpeakingMsg,
    encode,
    parse,
)

log = logging.getLogger(__name__)


class Handlers:
    """Callbacks the server invokes. Wired up in main.py."""

    def __init__(
        self,
        on_head_pose: Callable[[HeadPoseMsg], None],
        on_gesture: Callable[[GestureMsg], None],
        on_say: Callable[[SayMsg], None],
        on_stop_speaking: Callable[[], None],
        gesture_names: list[str],
    ) -> None:
        self.on_head_pose = on_head_pose
        self.on_gesture = on_gesture
        self.on_say = on_say
        self.on_stop_speaking = on_stop_speaking
        self.gesture_names = gesture_names

    def dispatch(self, msg: Incoming) -> None:
        if isinstance(msg, HeadPoseMsg):
            self.on_head_pose(msg)
        elif isinstance(msg, GestureMsg):
            self.on_gesture(msg)
        elif isinstance(msg, SayMsg):
            self.on_say(msg)
        elif isinstance(msg, StopSpeakingMsg):
            self.on_stop_speaking()


class BridgeServer:
    def __init__(self, cfg: BridgeCfg, state: StateStore, handlers: Handlers) -> None:
        self.cfg = cfg
        self.state = state
        self.handlers = handlers
        self._clients: set = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stopped: asyncio.Event | None = None

    # ---- lifecycle ----
    def start(self) -> None:
        self._thread = threading.Thread(target=self._thread_main, name="bridge", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._loop and self._stopped:
            self._loop.call_soon_threadsafe(self._stopped.set)
        if self._thread:
            self._thread.join(timeout=2)

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        self._stopped = asyncio.Event()
        async with websockets.serve(self._handle, self.cfg.host, self.cfg.port):
            log.info("Bridge listening on ws://%s:%d", self.cfg.host, self.cfg.port)
            broadcaster = asyncio.create_task(self._broadcast_state())
            await self._stopped.wait()
            broadcaster.cancel()

    # ---- connections ----
    async def _handle(self, ws) -> None:
        self._clients.add(ws)
        await ws.send(encode("hello", version=PROTOCOL_VERSION, gestures=self.handlers.gesture_names))
        try:
            async for raw in ws:
                try:
                    self.handlers.dispatch(parse(raw))
                except (ProtocolError, KeyError) as e:
                    await ws.send(encode("error", message=str(e)))
        except websockets.ConnectionClosed:
            pass
        finally:
            self._clients.discard(ws)

    async def _broadcast_state(self) -> None:
        period = 1.0 / self.cfg.state_rate_hz
        while True:
            if self._clients:
                msg = encode("state", **self.state.get().to_dict())
                websockets.broadcast(self._clients, msg)
            await asyncio.sleep(period)
