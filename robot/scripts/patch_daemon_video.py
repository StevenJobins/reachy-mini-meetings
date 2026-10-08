"""Sharper robot video: patch the Reachy Mini daemon's webrtcsink (reachy_mini 1.11, macOS desktop app).

The daemon feeds 1080p60 raw frames to webrtcsink without any settings, so it starts at 2 Mbit/s, may go down
to 1 kbit/s and up to 8 Mbit/s, and below 2 Mbit/s it scales the picture down to 720p / 360p: washed out in
the headset. There is no daemon option for this, so this script edits media_server.py of the installed app
(an app update overwrites it: run the script again). Restart the daemon (Reachy Mini Control) afterwards.

    python robot/scripts/patch_daemon_video.py             # bitrate 3-15 Mbit/s (start 6)
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
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args()
    f, orig = args.file, args.file.with_suffix(".py.orig")
    if args.revert:
        shutil.copy(orig, f)
        print("restored", f)
        return
    src = f.read_text()
    if BEGIN in src:   # already patched: replace the old block
        src = src[:src.index(BEGIN)] + src[src.index(END) + len(END):]
    elif not orig.exists():
        shutil.copy(f, orig)
    if ANCHOR not in src:
        raise SystemExit(f"anchor not found in {f}: daemon version changed, patch by hand")
    f.write_text(src.replace(ANCHOR, ANCHOR + block(args.h264)))
    print(f"patched {f} (h264={args.h264}); restart the daemon")


if __name__ == "__main__":
    main()
