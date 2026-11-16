"""Share this laptop's screen with the headset: the screen as a second video stream, next to Reachy's camera.

The screen goes through its own webrtcsink with its own signalling server on 127.0.0.1:8445, not through the
daemon's on 8443: the camera stream stays as it is, and a headset can never take one stream for the other. The
headset reaches it through the gateway (/screen) and shows it on a panel while this runs (ScreenShare.cs).
Run it with the Python of the Reachy Mini Control app, which ships GStreamer with webrtcsink (like fake_camera.py):

    PY="$HOME/Library/Application Support/com.pollen-robotics.reachy-mini/.venv/bin/python"
    "$PY" robot/scripts/share_screen.py             # until Ctrl+C (start_meeting.sh --share and the Mac app do this)

macOS asks once for "Screen Recording" for the program that starts this (Terminal or the Mac app). 1280 px wide,
30 pictures a second, VP8, 1 to 3 Mbit/s: a second decoder in the headset next to the 1080p camera has to stay cheap.
Only on the same network as the laptop: the video itself goes straight to the headset (UDP), and unlike the daemon
this sender has no relay servers, so a headset that came in through --remote gets no picture.

Without the framerate in the caps avfvideosrc delivered no frame at all (2026-11-16, GStreamer 1.28.9).
"""

import argparse
import signal

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

PORT = 8445
NAME = "screen"


def screen_size(display: int, fps: int) -> tuple[int, int]:
    """Pixels of the screen as avfvideosrc delivers it (one frame is pulled to find out)."""
    probe = Gst.parse_launch(f"avfvideosrc capture-screen=true device-index={display} "
                             f"! video/x-raw,framerate={fps}/1 ! fakesink name=s num-buffers=1")
    probe.set_state(Gst.State.PLAYING)
    msg = probe.get_bus().timed_pop_filtered(10 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
    caps = probe.get_by_name("s").get_static_pad("sink").get_current_caps()
    probe.set_state(Gst.State.NULL)
    if msg is None or msg.type == Gst.MessageType.ERROR or caps is None:
        raise SystemExit("no picture of the screen: allow Screen Recording for this program in System Settings > "
                         "Privacy & Security")
    st = caps.get_structure(0)
    return st.get_value("width"), st.get_value("height")


def out_height(width: int, screen: tuple[int, int]) -> int:
    """Height that keeps the screen's shape, even (the encoder needs that)."""
    return max(2, round(width * screen[1] / screen[0] / 2) * 2)


def launch_line(width: int, height: int, fps: int, port: int, display: int) -> str:
    # width alone in the caps made videoscale fail ("integer overflow"), so the height is worked out before
    return (f"avfvideosrc capture-screen=true capture-screen-cursor=true device-index={display} "
            f"! video/x-raw,framerate={fps}/1 ! videoconvert ! videoscale "
            f"! video/x-raw,width={width},height={height},pixel-aspect-ratio=1/1 "
            f"! webrtcsink name=ws run-signalling-server=true signalling-server-host=127.0.0.1 "
            f"signalling-server-port={port} video-caps=video/x-vp8 "
            f"start-bitrate=2000000 min-bitrate=1000000 max-bitrate=3000000")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--display", type=int, default=0, help="which screen, 0 = the main one")
    args = ap.parse_args()

    Gst.init(None)
    height = out_height(args.width, screen_size(args.display, args.fps))
    pipeline = Gst.parse_launch(launch_line(args.width, height, args.fps, args.port, args.display))
    ws = pipeline.get_by_name("ws")
    ws.set_property("meta", Gst.Structure.new_from_string(f"meta,name={NAME}"))
    ws.connect("consumer-added", lambda _ws, peer, _bin: print("consumer added", peer, flush=True))
    ws.connect("consumer-removed", lambda _ws, peer, _bin: print("consumer removed", peer, flush=True))

    loop = GLib.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()

    def on_message(_bus, msg):
        if msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            print("error:", err.message, dbg, flush=True)
            loop.quit()

    bus.connect("message", on_message)
    for sig in (signal.SIGINT, signal.SIGTERM):   # stopped by the Mac app or start_meeting.sh: give the screen back
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, loop.quit)
    pipeline.set_state(Gst.State.PLAYING)
    print(f"sharing screen {args.display}, {args.width}x{height}, {args.fps} fps, signalling ws://127.0.0.1:{args.port}",
          flush=True)
    try:
        loop.run()
    finally:
        pipeline.set_state(Gst.State.NULL)


if __name__ == "__main__":
    main()
