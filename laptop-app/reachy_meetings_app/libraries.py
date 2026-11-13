"""First start of the app that brings its own Python: fetch the backend's libraries (speech recognition, faces,
voices: about 1.6 GB) into ~/Library/Application Support/Reachy Meetings, once.

They are not inside the app: frozen in, these libraries are where bundling goes wrong, and the app would be a
2 GB download for every change of ours. So the app carries only Python and our code, and installs the exact
versions the backend was tested with (requirements-backend.txt, the list of a working environment) with pip into a
folder of its own. The backend then runs with that folder on its path. A new list means a new folder: an update
that needs other libraries fetches them and removes the old ones.

The models (about 2 GB more) are fetched by the backend itself when it first needs them, as always.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# REACHY_SUPPORT_DIR: another place, to try the first start without touching the real one
SUPPORT = Path(os.environ.get("REACHY_SUPPORT_DIR") or Path.home() / "Library" / "Application Support" / "Reachy Meetings")
COLLECTING = re.compile(r"^Collecting ([A-Za-z0-9_.\-]+)")
DOWNLOADING = re.compile(r"^\s*Downloading (\S+?)(?:-\d[^ ]*)? \(([0-9.]+ [kMG]B)\)")


def standalone() -> bool:
    """The app with its own Python (launcher.c sets this); else we run from a checkout with the backend's environment."""
    return os.environ.get("REACHY_STANDALONE") == "1"


def requirements_file(repo: Path) -> Path:
    return repo / "laptop-app" / "requirements-backend.txt"


def wanted(requirements: str) -> list:
    """The pinned lines of the list, without comments and blanks."""
    return [line.strip() for line in requirements.splitlines() if line.strip() and not line.startswith("#")]


def libs_dir(requirements: str, support: Path = SUPPORT) -> Path:
    """One folder per list of libraries, named after the list."""
    return support / ("libs-" + hashlib.sha256("\n".join(wanted(requirements)).encode()).hexdigest()[:12])


def complete(folder: Path) -> bool:
    return (folder / ".complete").is_file()


class Progress:
    """What pip is doing, from its output: how far (0..1) and a line for the user."""

    def __init__(self, total: int) -> None:
        self.total, self.seen, self.fraction, self.text = max(total, 1), set(), 0.0, "Getting ready …"

    def line(self, line: str) -> None:
        found = COLLECTING.match(line)
        if found:
            self.seen.add(found.group(1).lower())
            self.fraction = min(0.9, 0.9 * len(self.seen) / self.total)   # the last tenth is the unpacking
            self.text = f"Fetching {found.group(1)} ({len(self.seen)} of {self.total})"
            return
        found = DOWNLOADING.match(line)
        if found:
            self.text = f"Downloading {found.group(1).split('-')[0]} ({found.group(2)}), {len(self.seen)} of {self.total}"
        elif line.startswith("Installing collected packages"):
            self.fraction, self.text = 0.93, "Unpacking the libraries …"
        elif line.startswith("Successfully installed"):
            self.fraction, self.text = 1.0, "Done"


def install(requirements_path: Path, folder: Path, report=lambda progress: None, python: str = sys.executable,
            popen=subprocess.Popen) -> None:
    """pip install the list into `folder`. Raises RuntimeError with pip's last lines if it fails; a folder that
    was not finished is started again from nothing next time."""
    names = wanted(requirements_path.read_text())
    progress = Progress(len(names))
    report(progress)
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    log_path = folder.parent / "setup.log"
    args = [python, "-m", "pip", "install", "--target", str(folder), "-r", str(requirements_path), "--only-binary", ":all:",
            "--no-input", "--disable-pip-version-check", "--progress-bar", "off", "--no-warn-script-location"]
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    last = []
    with open(log_path, "w") as log:
        proc = popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, env=env)
        for line in proc.stdout:
            log.write(line)
            last = (last + [line.rstrip()])[-6:]
            progress.line(line)
            report(progress)
        code = proc.wait()
    if code != 0:
        raise RuntimeError("The libraries could not be installed (is this Mac online?).\n" + "\n".join(last)[-600:])
    (folder / ".complete").write_text("\n".join(names) + "\n")
    for old in folder.parent.glob("libs-*"):   # libraries of an earlier list
        if old != folder:
            shutil.rmtree(old, ignore_errors=True)
