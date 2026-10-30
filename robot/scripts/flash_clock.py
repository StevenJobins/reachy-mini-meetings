"""Video latency with the real camera: flash_clock.html flips a screen black/white on exact clock ticks, Reachy films
it, and the Unity app (FlashLatency.cs) compares the flips it sees in the video with this Mac's clock.

Run it on the robot laptop, with the Python of the Reachy Mini Control app (it has websockets):

    PY="$HOME/Library/Application Support/com.pollen-robotics.reachy-mini/.venv/bin/python"
    "$PY" robot/scripts/flash_clock.py

Opens the page, then answers on ws://0.0.0.0:8444 with this Mac's Unix time in ms (the same clock as Date.now() in
the page). The screen must be in the camera's view and the window in front: Chrome throttles covered windows.
"""

import asyncio
import subprocess
import time
from pathlib import Path

import websockets

CLOCK_PORT = 8444


async def handler(ws) -> None:
    async for _ in ws:
        await ws.send(str(int(time.time() * 1000)))


async def main() -> None:
    subprocess.run(["open", str(Path(__file__).with_name("flash_clock.html"))], check=False)
    async with websockets.serve(handler, "0.0.0.0", CLOCK_PORT):
        print(f"clock on ws://0.0.0.0:{CLOCK_PORT}", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
