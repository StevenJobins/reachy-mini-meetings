"""Stand-in for the daemon's camera stream: a test pattern through webrtcsink, same signalling as the daemon.

For testing headset clients (xr-client/unity) without the robot: webrtcsink with its built-in signalling server on
:8443, producer meta name "reachymini", raw 1080p60 frames in, webrtcsink encodes (VP8 by default, like the daemon).
Run it with the Python of the Reachy Mini Control app, which ships GStreamer with webrtcsink:

    PY="$HOME/Library/Application Support/com.pollen-robotics.reachy-mini/.venv/bin/python"
    "$PY" robot/scripts/fake_camera.py              # VP8, like the stock daemon

Latency measurement (xr-client/unity, VideoLatency.cs): every frame carries this Mac's clock as a time strip along
its top edge, 44 blocks of 40x40 px from x=0, most significant bit first, white = 1, value = Unix time in ms
mod 2^44, drawn right before the frame goes into webrtcsink. The headset syncs its clock to this Mac over
ws://<mac>:8444 (send anything, the reply is the Mac's Unix time in ms) and compares.

--h264 asks webrtcsink for H264, but on macOS (GStreamer 1.28, Apple vtenc) its codec discovery fails with
"not-negotiated" for every profile (2026-10-28), so only VP8 works from a Mac.
"""

import argparse
import asyncio
import threading
import time

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

STRIP_BITS = 44
STRIP_BLOCK = 40  # px, in the 1920-wide frame
CLOCK_PORT = 8444


def draw_strip(_overlay, ctx, _timestamp, _duration) -> None:
    ms = int(time.time() * 1000) % (1 << STRIP_BITS)
    for i in range(STRIP_BITS):
        bit = (ms >> (STRIP_BITS - 1 - i)) & 1
        ctx.set_source_rgb(bit, bit, bit)
        ctx.rectangle(i * STRIP_BLOCK, 0, STRIP_BLOCK, STRIP_BLOCK)
        ctx.fill()


def run_clock_server() -> None:
    import websockets

    async def handler(ws):
        async for _ in ws:
            await ws.send(str(int(time.time() * 1000)))

    async def serve():
        async with websockets.serve(handler, "0.0.0.0", CLOCK_PORT):
            await asyncio.Future()

    asyncio.run(serve())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--h264", action="store_true", help="force H264 instead of VP8 (fails on macOS, see above)")
    args = ap.parse_args()

    Gst.init(None)
    caps = "video/x-h264" if args.h264 else "video/x-vp8"
    # Moving ball + running time: frozen or stuttering video is obvious in the headset
    pipeline = Gst.parse_launch(
        f"videotestsrc is-live=true pattern=ball background-color=0xff303850 "
        f"! video/x-raw,width={args.width},height={args.height},framerate={args.fps}/1 "
        f"! timeoverlay font-desc=\"Sans 48\" halignment=center valignment=center "
        f"! videoconvert ! cairooverlay name=strip ! videoconvert "
        f"! webrtcsink name=ws run-signalling-server=true video-caps={caps} "
        f"start-bitrate=6000000 min-bitrate=3000000 max-bitrate=15000000"
    )
    pipeline.get_by_name("strip").connect("draw", draw_strip)
    ws = pipeline.get_by_name("ws")
    ws.set_property("meta", Gst.Structure.new_from_string("meta,name=reachymini"))
    ws.connect("consumer-added", lambda _ws, peer, _bin: print("consumer added", peer, flush=True))
    ws.connect("consumer-removed", lambda _ws, peer, _bin: print("consumer removed", peer, flush=True))
    threading.Thread(target=run_clock_server, daemon=True).start()

    loop = GLib.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()

    def on_message(_bus, msg):
        if msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            print("error:", err.message, dbg, flush=True)
            loop.quit()

    bus.connect("message", on_message)
    pipeline.set_state(Gst.State.PLAYING)
    print(f"fake camera {args.width}x{args.height}@{args.fps} {caps}, signalling ws://0.0.0.0:8443, "
          f"clock ws://0.0.0.0:{CLOCK_PORT}", flush=True)
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    pipeline.set_state(Gst.State.NULL)


if __name__ == "__main__":
    main()
