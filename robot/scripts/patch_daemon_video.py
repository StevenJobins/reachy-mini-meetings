"""Sharper robot video: patch the Reachy Mini daemon's webrtcsink (reachy_mini 1.11, macOS desktop app).

The daemon feeds 1080p60 raw frames to webrtcsink without any settings, so it starts at 2 Mbit/s, may go down
to 1 kbit/s and up to 8 Mbit/s, and below 2 Mbit/s it scales the picture down to 720p / 360p: washed out in
the headset. There is no daemon option for this, so this script edits media_server.py of the installed app
(an app update overwrites it: run the script again). Restart the daemon (Reachy Mini Control) afterwards.

    python robot/scripts/patch_daemon_video.py             # bitrate 3-15 Mbit/s (start 6)
    python robot/scripts/patch_daemon_video.py --fps 30    # + videorate to 30 fps: DON'T, WebRTC sessions then hung
                                                           #   mid-negotiation after ~1 min (2026-10-08)
    python robot/scripts/patch_daemon_video.py --h264      # + H264 (Apple hardware encoder instead of VP8 software)
    python robot/scripts/patch_daemon_video.py --revert    # original file back
"""

import argparse
import shutil
from pathlib import Path

DEFAULT = Path.home() / ("Library/Application Support/com.pollen-robotics.reachy-mini/.venv/lib/python3.12/"
                         "site-packages/reachy_mini/media/media_server.py")
ANCHOR = '        webrtcsink.set_property("run-signalling-server", True)\n'
BEGIN, END = "        # >>> reachy-meetings video patch\n", "        # <<< reachy-meetings video patch\n"
# WebRTC branch (macOS path): raw 1080p60 frames go straight into webrtcsink
FPS_ANCHOR = "            # Feed raw video, let webrtcsink handle encoding\n            queue_webrtc.link(webrtcsink)\n"
FPS_BEGIN, FPS_END = "            # >>> reachy-meetings fps patch\n", "            # <<< reachy-meetings fps patch\n"


def fps_block(fps: int) -> str:
    # 60 fps of 1080p was too much for the headset (VR dropped to ~19 fps while decoding and uploading it) and
    # splits the bitrate over twice the frames; 30 fps gives sharper frames at the same bitrate.
    return (FPS_BEGIN
            + '            rate = Gst.ElementFactory.make("videorate")\n'
            + '            rate.set_property("drop-only", True)\n'
            + '            caps = Gst.ElementFactory.make("capsfilter")\n'
            + f'            caps.set_property("caps", Gst.Caps.from_string("video/x-raw,framerate={fps}/1"))\n'
            + "            pipeline.add(rate)\n"
            + "            pipeline.add(caps)\n"
            + "            queue_webrtc.link(rate)\n"
            + "            rate.link(caps)\n"
            + "            caps.link(webrtcsink)\n"
            + FPS_END)


def block(h264: bool) -> str:
    lines = [
        BEGIN,
        "        # robot/scripts/patch_daemon_video.py: min 3 Mbit/s keeps webrtcsink from scaling down to 720p/360p\n",
        '        webrtcsink.set_property("start-bitrate", 6_000_000)\n',
        '        webrtcsink.set_property("min-bitrate", 3_000_000)\n',
        '        webrtcsink.set_property("max-bitrate", 15_000_000)\n',
    ]
    if h264:
        lines.append('        webrtcsink.set_property("video-caps", Gst.Caps.from_string("video/x-h264"))\n')
    return "".join(lines) + END


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, default=DEFAULT)
    ap.add_argument("--h264", action="store_true")
    ap.add_argument("--fps", type=int, default=0, help="video frame rate to the headset; 0 = camera rate (60)")
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args()
    f, orig = args.file, args.file.with_suffix(".py.orig")
    if args.revert:
        shutil.copy(orig, f)
        print("restored", f)
        return
    src = f.read_text()
    if not orig.exists():
        shutil.copy(f, orig)
    src = orig.read_text()   # always patch the original, so running it again changes the settings cleanly
    if ANCHOR not in src or FPS_ANCHOR not in src:
        raise SystemExit(f"anchor not found in {f}: daemon version changed, patch by hand")
    src = src.replace(ANCHOR, ANCHOR + block(args.h264))
    if args.fps:
        src = src.replace(FPS_ANCHOR, "            # Feed raw video, let webrtcsink handle encoding\n" + fps_block(args.fps))
    f.write_text(src)
    print(f"patched {f} (h264={args.h264}, fps={args.fps or 'camera'}); restart the daemon")


if __name__ == "__main__":
    main()
