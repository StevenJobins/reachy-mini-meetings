"""Prints the caption stream, like the headset would receive it.

    python scripts/print_captions.py [--url ws://localhost:8766]
"""

import argparse
import asyncio
import json

import websockets


async def main(url: str) -> None:
    async with websockets.connect(url) as ws:
        async for raw in ws:
            m = json.loads(raw)
            if m["type"] != "caption":
                print("server:", m)
                continue
            az = "   ?" if m["azimuth_deg"] is None else f"{m['azimuth_deg']:+4.0f}"
            kind = "FINAL" if m["final"] else "  ..."
            line = f"#{m['id']:<3} {kind} az={az}° [{m['lang']}] {m['text']}"
            if m["translation"]:
                line += f"\n{'':22}[{m['target']}] {m['translation']}"
            print(line, flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://localhost:8766")
    try:
        asyncio.run(main(ap.parse_args().url))
    except (KeyboardInterrupt, websockets.ConnectionClosed):
        pass
