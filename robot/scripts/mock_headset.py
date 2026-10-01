"""Fake headset: connects to the bridge and sends a slow look-around + a few gestures.

Use it to test the robot side before the xr-client exists.

    reachy-meetings --no-media                 # terminal 1 (with the daemon running)
    python scripts/mock_headset.py             # terminal 2
    python scripts/mock_headset.py --url ws://<robot-laptop-ip>:8765
"""

import argparse
import asyncio
import json
import math
import time

import websockets


async def main(url: str, seconds: float) -> None:
    async with websockets.connect(url) as ws:
        print("server:", await ws.recv())

        async def printer():
            async for raw in ws:
                msg = json.loads(raw)
                if msg["type"] == "state":
                    print(f"\rrobot yaw={msg['head_yaw_deg']:6.1f} pitch={msg['head_pitch_deg']:6.1f}"
                          f" body={msg['body_yaw_deg']:6.1f} gesture={msg['active_gesture']}   ",
                          end="")
                else:
                    print("\nserver:", msg)

        printer_task = asyncio.create_task(printer())
        t0 = time.time()
        next_gesture = 3.0
        gestures = iter(["nod", "tilt_curious", "antennas_happy", "shake", "attention"])
        while (t := time.time() - t0) < seconds:
            await ws.send(json.dumps({
                "type": "head_pose",
                "roll": 0.0,
                "pitch": 10 * math.sin(2 * math.pi * t / 5),
                "yaw": 50 * math.sin(2 * math.pi * t / 8),
                "t": time.time(),
            }))
            if t > next_gesture:
                g = next(gestures, None)
                if g:
                    await ws.send(json.dumps({"type": "gesture", "name": g, "duration": 1.0}))
                next_gesture += 3.0
            await asyncio.sleep(1 / 60)  # ~Quest frame rate
        printer_task.cancel()
    print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://localhost:8765")
    ap.add_argument("--seconds", type=float, default=20)
    a = ap.parse_args()
    asyncio.run(main(a.url, a.seconds))
