"""The window's ear on the backend, against a real WebSocket server on this machine.   pytest -q"""

import asyncio
import json
import threading
import time

import pytest

websockets = pytest.importorskip("websockets")

from reachy_meetings_app.live import Live


def test_live_gets_json_messages_skips_sound_and_survives_long_ones():
    got, port, loop_ready = [], [], threading.Event()

    async def handler(ws):
        await ws.send(json.dumps({"type": "hello"}))
        await ws.send(b"\x00\x01" * 640)                               # room sound
        await ws.send(json.dumps({"type": "caption", "text": "ä" * 70000}))   # longer than one small frame
        await ws.ping()                                                # must be answered
        await ws.send(json.dumps({"type": "me_vad", "speaking": True}))
        await asyncio.sleep(1)

    async def serve():
        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port.append(server.sockets[0].getsockname()[1])
            loop_ready.set()
            await asyncio.sleep(3)

    threading.Thread(target=lambda: asyncio.run(serve()), daemon=True).start()
    assert loop_ready.wait(5)
    live = Live(got.append, port=port[0], retry_s=0.1)
    live.start()
    deadline = time.time() + 5
    while len(got) < 3 and time.time() < deadline:
        time.sleep(0.05)
    live.stop()
    assert [m["type"] for m in got] == ["hello", "caption", "me_vad"]
    assert len(got[1]["text"]) == 70000 and got[2]["speaking"] is True


def test_live_keeps_trying_while_nothing_listens():
    live = Live(lambda m: None, port=1, retry_s=0.05)
    live.start()
    time.sleep(0.3)
    assert live.is_alive() and not live.connected
    live.stop()
