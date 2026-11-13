"""python -m reachy_meetings_app            the app: starts gateway and backend, shows the window, stops them on quit
python -m reachy_meetings_app --snapshot window.png [--demo]   the window as a picture, nothing started
python -m reachy_meetings_app --version   which code runs (the app's own or an update it fetched)
"""

import argparse

from . import updates
from .supervisor import Status, Supervisor
from .window import run


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", default="", help="write the window as a PNG to this path and leave")
    ap.add_argument("--demo", action="store_true", help="with --snapshot: a made-up status with every part on show")
    ap.add_argument("--demo-setup", action="store_true", help="with --snapshot: the first start, libraries being fetched")
    ap.add_argument("--version", action="store_true", help="print the version of the code that runs and where it is, and leave")
    args = ap.parse_args()
    if args.version or args.snapshot:
        updates.mark_ok(updates.running_root())   # no real start: says nothing about whether this code works
    if args.version:
        print(updates.version_of(updates.running_root()), updates.running_root())
        return
    if args.demo_setup:
        run(Supervisor(), args.snapshot, Status(robot="running", camera=True, door=True, address="192.168.1.20", code="493 201",
                                                 setup="Downloading torch (554.0 MB), 31 of 82", setup_fraction=0.34))
        return
    demo = Status(robot="running", camera=True, backend=True, faces="1.0 faces in view", door=True,
                  address="192.168.1.20", remote_wanted=True, remote_host="example.trycloudflare.com", code="493 201",
                  known=["Meta Quest 3 51c0", "samsung SM-I610 aba7"], connected=["samsung SM-I610 aba7"],
                  translation_key=True, version="0.2.0", update="0.3.0") if args.demo else None
    run(Supervisor(), args.snapshot, demo)


if __name__ == "__main__":
    main()
