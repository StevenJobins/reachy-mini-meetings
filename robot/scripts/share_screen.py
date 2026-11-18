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

One window or one app instead of the whole screen (the Mac app's share menu, since 2026-11-18): --source window:<id>,
app:<pid> or display:<id> with --helper, the app's screencap (laptop-app/screencap.swift, ScreenCaptureKit). It
captures and scales; its raw NV12 frames come in here through a pipe and an appsrc. GStreamer's avfvideosrc can only
take whole screens, and Reachy Mini Control's Python has no ScreenCaptureKit.
"""

import argparse
import signal
import subprocess
import threading

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


def helper_line(width: int, height: int, fps: int, port: int) -> str:
    return (f"appsrc name=src is-live=true do-timestamp=true format=time block=true max-bytes={width * height * 3} "
            f"caps=video/x-raw,format=NV12,width={width},height={height},framerate={fps}/1,pixel-aspect-ratio=1/1 "
            f"! queue leaky=downstream max-size-buffers=2 ! videoconvert "
            f"! webrtcsink name=ws run-signalling-server=true signalling-server-host=127.0.0.1 "
            f"signalling-server-port={port} video-caps=video/x-vp8 "
            f"start-bitrate=2000000 min-bitrate=1000000 max-bitrate=3000000")


def read_exactly(stream, n: int) -> bytes:
    data = bytearray()
    while len(data) < n:
        chunk = stream.read(n - len(data))
        if not chunk:
            return b""
        data += chunk
    return bytes(data)


def start_helper(helper: str, source: str, width: int, fps: int, thumb: str):
    """The app's screencap for one window, app or screen: (process, width, height) once it delivers."""
    kind, _, ident = source.partition(":")
    args = [helper, kind, ident, "--width", str(width), "--fps", str(fps)] + (["--thumb", thumb] if thumb else [])
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    head = proc.stdout.readline().decode().split()
    if len(head) != 4 or head[0] != "nv12":
        proc.wait()
        raise SystemExit(proc.stderr.read().decode(errors="replace").strip() or f"screencap ended (exit {proc.returncode})")
    return proc, int(head[1]), int(head[2])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--display", type=int, default=0, help="which screen, 0 = the main one")
    ap.add_argument("--source", default="", help="window:<id>, app:<pid> or display:<id>, through --helper")
    ap.add_argument("--helper", default="", help="the Mac app's screencap program")
    ap.add_argument("--thumb", default="", help="screencap writes a small picture of what is shared here")
    args = ap.parse_args()

    Gst.init(None)
    helper, reason = None, []
    if args.source and args.helper:
        helper, width, height = start_helper(args.helper, args.source, args.width, args.fps, args.thumb)
        pipeline = Gst.parse_launch(helper_line(width, height, args.fps, args.port))
    else:
        width = args.width
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
    if helper is not None:
        src, size = pipeline.get_by_name("src"), width * height * 3 // 2

        def pump():   # frames from screencap into the pipeline; it ending (window closed) ends this
            while True:
                frame = read_exactly(helper.stdout, size)
                if not frame or src.emit("push-buffer", Gst.Buffer.new_wrapped(frame)) != Gst.FlowReturn.OK:
                    break
            helper.wait()
            reason.append(helper.stderr.read().decode(errors="replace").strip() or f"screencap ended ({helper.returncode})")
            GLib.idle_add(loop.quit)

        threading.Thread(target=pump, daemon=True).start()
    print(f"sharing {args.source or f'screen {args.display}'}, {width}x{height}, {args.fps} fps, "
          f"signalling ws://127.0.0.1:{args.port}", flush=True)
    try:
        loop.run()
    finally:
        pipeline.set_state(Gst.State.NULL)
        if helper is not None and helper.poll() is None:
            helper.terminate()
        if reason:   # the last line of the log: the Mac app shows it
            print(reason[-1], flush=True)


if __name__ == "__main__":
    main()
