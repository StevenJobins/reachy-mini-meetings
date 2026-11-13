"""The app updates its own code (the standalone app only; a checkout updates with `git pull`).

What is updated is our code: this app, the gateway, the backend (the folder the app carries as Resources/code).
Python and the app's program stay as installed; the backend's libraries follow by themselves when a new code
brings another list (libraries.py). So an update is a few hundred kB, not gigabytes.

Where from: three files of the project's latest GitHub release (release.sh makes them):
    latest.json                              {"version": "0.3.0", "file": "reachy-meetings-code-0.3.0.tar.gz"}
    reachy-meetings-code-<version>.tar.gz    the code, laid out like Resources/code
    reachy-meetings-code-<version>.tar.gz.sig   its signature (ECDSA P-256 / SHA-256, by release_key.pem)

What the app takes: only a package signed with the key whose public half it carries (release_key.pub.pem), whose
own VERSION is the announced one and newer than what runs. Whoever can publish a release but does not have the
key cannot give the laptops next to the robots other code, and an old package cannot be served again as new.

Where it goes: ~/Library/Application Support/Reachy Meetings/code/<version>/, and `current` names the version to
run. It is used from the next start: launcher.c asks choose() which code to run. An update that does not get as far
as its window is not tried a second time: the app goes back to the code it was installed with.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from .libraries import SUPPORT

BASE_URL = os.environ.get("REACHY_UPDATE_URL") or "https://github.com/StevenJobins/reachy-mini-meetings/releases/latest/download"
ENTRY = Path("laptop-app") / "reachy_meetings_app" / "__main__.py"


def parse(version: str) -> tuple:
    """ "0.3.0" -> (0, 3, 0); anything else -> () and so older than every real version."""
    try:
        return tuple(int(part) for part in version.strip().split("."))
    except (ValueError, AttributeError):
        return ()


def newer(a: str, b: str) -> bool:
    return bool(parse(a)) and parse(a) > parse(b)


def version_of(root: Path) -> str:
    """The version of the code in `root` (a checkout, Resources/code, or a downloaded folder); "" if it has none."""
    try:
        return (root / "laptop-app" / "VERSION").read_text().strip()
    except OSError:
        return ""


def running_root() -> Path:
    return Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- which code to run (asked by launcher.c)
def choose(bundled: Path, support: Path = SUPPORT) -> Path:
    """The downloaded code named by `current`, if it is whole, newer than the app's own and has not failed; else
    the app's own. A folder still marked as starting was started before and never reached its window: it failed."""
    try:
        folder = support / "code" / (support / "code" / "current").read_text().strip()
    except OSError:
        return bundled
    if folder.parent != support / "code" or not (folder / ENTRY).is_file():
        return bundled
    if not newer(version_of(folder), version_of(bundled)):   # a newer app was installed since: its own code wins
        return bundled
    if (folder / ".starting").exists():
        (folder / ".failed").write_text("did not reach its window\n")
    return bundled if (folder / ".failed").exists() else folder


def mark_starting(root: Path) -> None:
    (root / ".starting").write_text("")


def mark_ok(root: Path) -> None:
    """The window is up and the parts were asked for their state: this code runs."""
    try:
        (root / ".starting").unlink()
    except OSError:
        pass


# ---------------------------------------------------------------- fetching one
def fetch(url: str, timeout: float = 30) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def verify(file: Path, signature: Path, public_key: Path) -> bool:
    """macOS's own openssl: the app's Python has no library for this until the backend's are fetched."""
    try:
        done = subprocess.run(["/usr/bin/openssl", "dgst", "-sha256", "-verify", str(public_key), "-signature",
                               str(signature), str(file)], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def available(current: str, base_url: str = BASE_URL, fetch=fetch):
    """{"version", "file"} of the latest release if it is newer than `current`, else None."""
    info = json.loads(fetch(f"{base_url}/latest.json"))
    version, file = info.get("version"), info.get("file")
    if not isinstance(version, str) or not isinstance(file, str) or "/" in file or not newer(version, current):
        return None
    return {"version": version, "file": file}


def install(info: dict, public_key: Path, support: Path = SUPPORT, base_url: str = BASE_URL, fetch=fetch,
            verify=verify) -> Path:
    """Download, check and unpack a release; name it as the one to run. Raises ValueError for a package that is not
    ours or not what was announced; nothing is changed then."""
    code = support / "code"
    code.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=code) as tmp:
        package, signature = Path(tmp) / "code.tar.gz", Path(tmp) / "code.tar.gz.sig"
        package.write_bytes(fetch(f"{base_url}/{info['file']}"))
        signature.write_bytes(fetch(f"{base_url}/{info['file']}.sig"))
        if not verify(package, signature, public_key):
            raise ValueError("the update is not signed with the project's key")
        unpacked = Path(tmp) / "unpacked"
        with tarfile.open(package) as tar:
            tar.extractall(unpacked, filter="data")   # no absolute paths, no links out of the folder
        if version_of(unpacked) != info["version"] or not (unpacked / ENTRY).is_file():
            raise ValueError("the update is not the version that was announced")
        folder = code / info["version"]
        if folder.exists():
            shutil.rmtree(folder)
        unpacked.rename(folder)
    (code / "current.tmp").write_text(info["version"] + "\n")
    os.replace(code / "current.tmp", code / "current")
    for old in code.iterdir():   # earlier downloads
        if old.is_dir() and old != folder:
            shutil.rmtree(old, ignore_errors=True)
    return folder
