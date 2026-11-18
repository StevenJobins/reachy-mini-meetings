"""What the app runs and watches on the laptop next to Reachy: the work of start_meeting.sh without a terminal.

    Reachy Mini Control   Pollen's app with the daemon; opened if it does not answer, never stopped by us
    gateway               robot/scripts/gateway.py: the door for headsets (pairing code, tokens, other networks)
    backend               captions, translation, faces, room sound (backend/)
    screen                robot/scripts/share_screen.py, only while the screen is shared (set_sharing); a window,
                          an app or a screen chosen in the window comes from the app's screencap (screencap.swift)

The app that brings its own Python (libraries.standalone()) first fetches the backend's libraries, once
(libraries.py); the gateway needs none of them and is up meanwhile, so a headset can already be paired.

No window in here (window.py shows what status() returns), so it runs in tests without a screen.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import libraries, updates

REPO = Path(__file__).resolve().parents[2]
LOGS = Path.home() / "Library" / "Logs" / "reachy-meetings"
SETTINGS = Path.home() / ".config" / "reachy-meetings" / "app.json"
DAEMON_PATCHED_FILE = Path.home() / ("Library/Application Support/com.pollen-robotics.reachy-mini/.venv/lib/python3.12/"
                                     "site-packages/reachy_mini/media/media_server.py")
PATCH_MARK = "# >>> reachy-meetings video patch"
# An app opened from the Finder gets a bare PATH: cloudflared (Homebrew) would not be found
EXTRA_PATH = "/opt/homebrew/bin:/usr/local/bin"
CONNECTION = re.compile(r"^(\S+) (.+) (daemon|signalling|backend|time|screen): (connected|closed)$")
# share_screen.py needs GStreamer with webrtcsink: the Python of Reachy Mini Control has it (like fake_camera.py)
SCREEN_PYTHON = Path.home() / "Library/Application Support/com.pollen-robotics.reachy-mini/.venv/bin/python"
FACES = re.compile(r"([0-9.]+) faces per frame, brightness ([0-9]+)")


def load_gateway(repo: Path = REPO):
    """robot/scripts/gateway.py as a module: its files and their formats are its business."""
    spec = importlib.util.spec_from_file_location("reachy_gateway", repo / "robot" / "scripts" / "gateway.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def connected_headsets(gateway_log: str) -> list:
    """Names of the headsets with a connection open right now, from the gateway's log of this run."""
    open_lines = {}
    for line in gateway_log.splitlines():
        found = CONNECTION.match(line)
        if found:
            name = found.group(2)
            open_lines[name] = open_lines.get(name, 0) + (1 if found.group(4) == "connected" else -1)
    return sorted(name for name, n in open_lines.items() if n > 0)


# Translation and meeting notes without keys of one's own: the project's hosted service (service/worker.js).
# Set to "" to switch it off: the backend then needs DEEPL_AUTH_KEY and GEMINI_API_KEY as before. REACHY_SERVICE_URL in the
# environment names another one. The service gives an hour a day for free; the backend writes LIMIT_LINE when it is over.
SERVICE_URL = "https://reachy-meetings.dominic-ill.workers.dev"
LIMIT_LINE = "Free time used up"
PLANS_URL = "https://stevenjobins.github.io/reachy-mini-meetings/#plans"


def faces_in_view(backend_log: str) -> str:
    """ "1.0 faces in view" from the backend's last vision line, or "" before the first."""
    found = FACES.findall(backend_log)
    return f"{found[-1][0]} faces in view" if found else ""


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def daemon_status() -> dict:
    """The daemon's own word, {} if it does not answer."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/daemon/status", timeout=2) as r:
            return json.load(r)
    except (OSError, ValueError):
        return {}


def wifi_address() -> str:
    try:
        return subprocess.run(["ipconfig", "getifaddr", "en0"], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def key_from_login_shell(name: str = "DEEPL_AUTH_KEY") -> str:
    """A key as the user's terminal has it (set in ~/.zshrc for the backend): an app opened from the
    Finder does not see that file. Read once, handed to the backend, never written anywhere."""
    try:
        out = subprocess.run(["/bin/zsh", "-lic", f'print -r -- "KEY=${name}"'], capture_output=True, text=True,
                             timeout=15, stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    lines = [line[4:] for line in out.splitlines() if line.startswith("KEY=")]
    return lines[-1].strip() if lines else ""


def tail(path: Path, lines: int = 4, width: int = 110) -> str:
    try:
        return "\n".join(line[:width] for line in path.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


# The language Reachy speaks for the headset user (Translate me): "auto" = the one most spoken in the room. Set here
# on the laptop, because the people in the room know it; passed to the backend as --meeting-lang. The headset sends
# "auto", which leaves this choice in place (captions.py meeting_lang()).
MEETING_LANGS = [("auto", "Auto (most spoken in the room)"), ("de", "Deutsch"), ("en", "English"), ("fr", "Français"),
                 ("it", "Italiano"), ("es", "Español")]


@dataclass
class Status:
    robot: str = ""              # "running", "starting", another state of the daemon, or "" = no answer
    robot_error: str = ""
    camera: bool = False         # the daemon's video signalling
    backend: bool = False
    faces: str = ""
    door: bool = False
    address: str = ""            # this laptop on its Wi-Fi
    remote_wanted: bool = False
    remote_host: str = ""        # the public address, "" while there is none
    remote_note: str = ""        # the gateway's last word on it, while there is none
    code: str = ""               # "123 456"
    known: list = field(default_factory=list)
    connected: list = field(default_factory=list)
    translation_key: bool = False
    notes_key: bool = True       # GEMINI_API_KEY for the meeting notes
    limit: bool = False          # the hosted service's free hour of today is used up: no translation, no notes
    install: str = ""            # this installation's id at the hosted service, "" when it is not used
    video_patch: bool = True
    problem: str = ""            # a part that stopped, with its last log lines
    version: str = ""            # of the code that runs
    update: str = ""             # a newer version that was fetched and is used from the next start
    setup: str = ""              # first start: what is being fetched; "" when there is nothing to set up
    setup_fraction: float = 0.0  # how far that is, 0..1
    meeting_lang: str = "auto"   # what Reachy speaks for the headset user (MEETING_LANGS)
    sharing: bool = False        # this laptop's screen goes to the headset
    share_note: str = ""         # why sharing stopped or cannot start
    share_label: str = ""        # what is shared, in words ("Safari: Budget 2027")
    share_thumb: str = ""        # a small picture of it, renewed every second while shared ("" without screencap)


class Supervisor:
    def __init__(self, repo: Path = REPO, logs: Path = LOGS, settings_path: Path = SETTINGS, python: str = sys.executable,
                 popen=subprocess.Popen, environ=None, shell_key=key_from_login_shell,
                 shell_notes_key=lambda: key_from_login_shell("GEMINI_API_KEY")) -> None:
        self.repo, self.logs, self.settings_path, self.python, self.popen = repo, logs, settings_path, python, popen
        self.environ = dict(os.environ if environ is None else environ)
        self.shell_key = shell_key
        self.shell_notes_key = shell_notes_key
        self.has_notes_key = True
        self.gateway_module = load_gateway(repo)
        self.gateway = self.backend = self.screen = None
        self.share_note, self.share_label = "", ""
        self.opened_control = False
        self.settings = self._load_settings()
        self.standalone = libraries.standalone() if environ is None else environ.get("REACHY_STANDALONE") == "1"
        self.support = libraries.SUPPORT
        self.setup_text, self.setup_fraction, self.setup_error = "", 0.0, ""
        self.version = updates.version_of(repo)
        self.update_ready = ""

    # ------------------------------------------------------------ settings
    def _load_settings(self) -> dict:
        try:
            data = json.loads(self.settings_path.read_text())
        except (OSError, ValueError):
            data = {}
        return data if isinstance(data, dict) else {}

    def _save_settings(self) -> None:
        self.gateway_module.write_private(self.settings_path, json.dumps(self.settings, indent=1))

    @property
    def remote(self) -> bool:
        return bool(self.settings.get("remote"))

    @property
    def meeting_lang(self) -> str:
        lang = self.settings.get("meeting_lang", "auto")
        return lang if lang in dict(MEETING_LANGS) else "auto"

    # ------------------------------------------------------------ the parts
    def install_id(self) -> str:
        """Made once, kept in the settings: what the hosted service counts the free time by."""
        if not self.settings.get("install"):
            import uuid

            self.settings["install"] = str(uuid.uuid4())
            self._save_settings()
        return self.settings["install"]

    def child_env(self) -> dict:
        env = dict(self.environ)
        env["PATH"] = env.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin") + ":" + EXTRA_PATH
        if env.get("REACHY_BUNDLE_BIN"):   # what the app brings along (cloudflared) comes first
            env["PATH"] = env["REACHY_BUNDLE_BIN"] + ":" + env["PATH"]
        env["PYTHONUNBUFFERED"] = "1"
        return env

    def _start(self, name: str, args: list, cwd: Path, env: dict):
        self.logs.mkdir(parents=True, exist_ok=True)
        log = open(self.logs / f"{name}.log", "w")
        return self.popen(args, cwd=str(cwd), env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)

    def start_gateway(self) -> None:
        args = [self.python, "-u", str(self.repo / "robot" / "scripts" / "gateway.py")] + (["--remote"] if self.remote else [])
        self.gateway = self._start("gateway", args, self.repo, self.child_env())

    def start_backend(self) -> None:
        env = self.child_env()
        env["PYTHONPATH"] = "src"
        if self.standalone:   # the app's own Python has none of the backend's libraries: they are in their folder
            env["PYTHONPATH"] += os.pathsep + str(self.libs())
        service = self.environ.get("REACHY_SERVICE_URL", SERVICE_URL)
        if service:   # the hosted service has the keys; it knows this installation by its id
            env["REACHY_SERVICE_URL"], env["REACHY_INSTALL_ID"] = service, self.install_id()
            env.pop("DEEPL_AUTH_KEY", None)
        elif self.settings.get("deepl_key"):                 # set in the app: that one counts
            env["DEEPL_AUTH_KEY"] = self.settings["deepl_key"]
        elif not env.get("DEEPL_AUTH_KEY"):
            key = self.shell_key()
            if key:
                env["DEEPL_AUTH_KEY"] = self.environ["DEEPL_AUTH_KEY"] = key   # kept for a restart, in memory only
        self.has_key = bool(service or env.get("DEEPL_AUTH_KEY"))
        if not service and not env.get("GEMINI_API_KEY"):   # the meeting notes; until 2026-11-18 only DeepL's key was looked up
            key = self.shell_notes_key()
            if key:
                env["GEMINI_API_KEY"] = self.environ["GEMINI_API_KEY"] = key
        self.has_notes_key = bool(service or env.get("GEMINI_API_KEY"))
        # Models already on this laptop: no questions to Hugging Face at the start (an expired login stops them loading)
        if (Path.home() / ".cache/huggingface/hub/models--mlx-community--whisper-large-v3-turbo").is_dir():
            env["HF_HUB_OFFLINE"] = "1"
        args = [self.python, "-m", "reachy_meetings_backend.captions"]
        if self.meeting_lang != "auto":
            args += ["--meeting-lang", self.meeting_lang]
        self.backend = self._start("backend", args, self.repo / "backend", env)

    def open_control(self) -> None:
        """Reachy Mini Control, if its daemon does not answer. Once: it takes a while to come up."""
        if self.opened_control or daemon_status():
            return
        self.opened_control = True
        try:
            subprocess.run(["open", "-a", "Reachy Mini Control"], timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass

    def libs(self) -> Path:
        return libraries.libs_dir(libraries.requirements_file(self.repo).read_text(), self.support)

    def prepare(self) -> bool:
        """First start of the app with its own Python: fetch the backend's libraries. True when they are there."""
        if not self.standalone or libraries.complete(self.libs()):
            return True

        def report(progress) -> None:
            self.setup_text, self.setup_fraction = progress.text, progress.fraction

        self.setup_text, self.setup_error = "Getting ready …", ""
        try:
            libraries.install(libraries.requirements_file(self.repo), self.libs(), report, self.python, self.popen)
        except (RuntimeError, OSError) as e:
            self.setup_error = str(e)
            return False
        finally:
            self.setup_text = ""
        return True

    def start(self) -> None:
        self.open_control()
        self.start_gateway()
        if self.prepare():
            self.start_backend()
        self.check_updates()

    def check_updates(self, base_url: str = updates.BASE_URL, fetch=updates.fetch, verify=updates.verify) -> None:
        """Is there a newer release of our code? Then fetch it; it is used from the next start (updates.py).
        Only the standalone app: a checkout updates with git. Being offline, or a release that is not ours, changes
        nothing and is only noted in update.log."""
        if not self.standalone or self.update_ready:
            return
        try:
            info = updates.available(self.version, base_url, fetch)
            if info is None or (self.support / "code" / info["version"] / ".failed").exists():
                return   # nothing newer, or one that did not start here before
            updates.install(info, self.repo / "laptop-app" / "release_key.pub.pem", self.support, base_url, fetch, verify)
            self.update_ready = info["version"]
            note = f"fetched {info['version']}, in use from the next start"
        except Exception as e:   # noqa: BLE001 - whatever the network or the release holds, the meeting goes on
            note = f"no update: {type(e).__name__}: {e}"
        try:
            self.logs.mkdir(parents=True, exist_ok=True)
            with open(self.logs / "update.log", "a") as log:
                log.write(note[:300] + "\n")
        except OSError:
            pass

    def bundle(self):
        """The .app this runs from (standalone), to open it again after an update; None from a checkout."""
        inside = self.environ.get("REACHY_BUNDLE_BIN")
        return Path(inside).parents[2] if inside else None

    def set_deepl_key(self, key: str) -> None:
        """The translation key, typed into the app. Kept in the app's settings (readable by this user only), and
        the backend starts again with it. An empty key takes it out again."""
        key = key.strip()
        if key:
            self.settings["deepl_key"] = key
        else:
            self.settings.pop("deepl_key", None)
        self._save_settings()
        if self.backend is not None:
            self._stop(self.backend)
            self.start_backend()

    @staticmethod
    def _stop(proc) -> None:
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    def stop(self) -> None:
        """Gateway, backend and a shared screen. Reachy Mini Control keeps running: the robot is its business."""
        self._stop(self.gateway)
        self._stop(self.backend)
        self._stop(self.screen)
        self.gateway = self.backend = self.screen = None

    # ------------------------------------------------------------ what the window asks for
    def set_remote(self, on: bool) -> None:
        """Open to other networks or not. The gateway starts again for it: headsets reconnect by themselves."""
        if on == self.remote:
            return
        self.settings["remote"] = bool(on)
        self._save_settings()
        if self.gateway is not None:
            self._stop(self.gateway)
            self.start_gateway()

    def screencap(self):
        """The app's program that captures one window, app or screen (ScreenCaptureKit); None without one, e.g. an
        app built before 2026-11-18 running newer code: then only the whole main screen can be shared."""
        bundle = self.bundle()
        for path in ([Path(self.environ["REACHY_SCREENCAP"])] if self.environ.get("REACHY_SCREENCAP") else []) + (
                [bundle / "Contents" / "MacOS" / "screencap"] if bundle else []) + [
                self.repo / "laptop-app" / "dist" / "Reachy Meetings.app" / "Contents" / "MacOS" / "screencap"]:
            if path.is_file():
                return path
        return None

    @property
    def thumb(self) -> Path:
        return self.logs / "screen.jpg"

    def shareable(self) -> dict:
        """{"displays": [...], "windows": [...]} that can be shared, from screencap; {} without it or permission."""
        helper = self.screencap()
        if helper is None:
            return {}
        try:
            out = subprocess.run([str(helper), "list"], capture_output=True, text=True, timeout=10)
            return json.loads(out.stdout) if out.returncode == 0 else {"error": out.stderr.strip()}
        except (OSError, subprocess.TimeoutExpired, ValueError) as e:
            return {"error": str(e)}

    def set_sharing(self, on: bool, python: Path = SCREEN_PYTHON, source: str = "", label: str = "") -> None:
        """This laptop's screen as a second picture in the headset, until switched off or the app quits. `source`:
        window:<id>, app:<pid> or display:<id> (through screencap), "" for the whole main screen. macOS asks once
        whether this app may record the screen."""
        self._stop(self.screen)
        self.screen, self.share_note, self.share_label = None, "", ""
        try:
            self.thumb.unlink()
        except OSError:
            pass
        if not on:
            return
        if not Path(python).exists():
            self.share_note = "Sharing needs Reachy Mini Control on this Mac (its video parts send the screen)."
            return
        args = [str(python), "-u", str(self.repo / "robot" / "scripts" / "share_screen.py")]
        helper = self.screencap()
        if source and helper is not None:
            args += ["--source", source, "--helper", str(helper), "--thumb", str(self.thumb)]
        self.share_label = label if source and helper is not None else "The whole screen"
        self.screen = self._start("screen", args, self.repo, self.child_env())

    def set_meeting_lang(self, lang: str) -> None:
        """The language Reachy speaks for the headset user. The backend starts again with it: captions pause for a few
        seconds and the headsets reconnect by themselves."""
        if lang not in dict(MEETING_LANGS) or lang == self.meeting_lang:
            return
        self.settings["meeting_lang"] = lang
        self._save_settings()
        if self.backend is not None:
            self._stop(self.backend)
            self.start_backend()

    def remove_headset(self, name: str) -> None:
        g = self.gateway_module
        headsets = g.load_headsets()
        if headsets.pop(name, None) is not None:
            g.save_headsets(headsets)

    def status(self) -> Status:
        g = self.gateway_module
        daemon = daemon_status()
        gateway_log = self.logs / "gateway.log"
        try:
            gateway_text = gateway_log.read_text(errors="replace")
        except OSError:
            gateway_text = ""
        try:
            backend_text = (self.logs / "backend.log").read_text(errors="replace")[-20000:]
        except OSError:
            backend_text = ""
        remote_lines = [line[8:] for line in gateway_text.splitlines() if line.startswith("remote: ")]
        s = Status(
            robot=daemon.get("state") or ("starting" if self.opened_control and not daemon else ""),
            robot_error=daemon.get("error") or "",
            camera=port_open(8443), backend=port_open(8766), door=port_open(g.PORT),
            faces=faces_in_view(backend_text), address=wifi_address(),
            remote_wanted=self.remote, remote_host=g.shown_remote(),
            remote_note=remote_lines[-1][:80] if remote_lines else "",
            code=g.shown_code(), known=sorted(g.load_headsets()), connected=connected_headsets(gateway_text),
            translation_key=getattr(self, "has_key", False) or bool(self.settings.get("deepl_key"))
            or bool(self.environ.get("DEEPL_AUTH_KEY")),
            notes_key=self.has_notes_key,
            limit=LIMIT_LINE in backend_text,
            install=self.settings.get("install", "") if self.environ.get("REACHY_SERVICE_URL", SERVICE_URL) else "",
            video_patch=self._patched(),
            setup=self.setup_text, setup_fraction=self.setup_fraction,
            version=self.version, update=self.update_ready,
            meeting_lang=self.meeting_lang,
            sharing=self.screen is not None and self.screen.poll() is None, share_note=self.share_note,
        )
        if s.sharing:
            s.share_label = self.share_label
            s.share_thumb = str(self.thumb) if self.thumb.is_file() else ""
        if self.screen is not None and self.screen.poll() is not None:   # it stopped by itself: say why, once
            s.share_note = self.share_note = "Sharing stopped: " + (tail(self.logs / "screen.log", 1, 200) or "no reason given")
            self.screen, self.share_label = None, ""
        if self.setup_error:
            s.problem = self.setup_error
        for name, proc, log in (("gateway", self.gateway, gateway_log), ("backend", self.backend, self.logs / "backend.log")):
            if proc is not None and proc.poll() is not None:
                s.problem = f"The {name} stopped.\n{tail(log)}"
        return s

    @staticmethod
    def _patched() -> bool:
        """The sharper-video patch in Pollen's daemon; an update of Reachy Mini Control removes it. True when the
        file is not there at all: nothing to say then."""
        try:
            return PATCH_MARK in DAEMON_PATCHED_FILE.read_text(errors="replace")
        except OSError:
            return True
