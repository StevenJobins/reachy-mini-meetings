"""The supervisor without a screen, a robot or real processes.   pytest -q"""

import json
import subprocess

import pytest

from reachy_meetings_app import supervisor as sup


@pytest.fixture(autouse=True)
def no_hosted_service(monkeypatch):
    """The tests about keys are about a laptop without the hosted service; the one about it names its own."""
    monkeypatch.setattr(sup, "SERVICE_URL", "")


class Proc:
    """Stands in for a started program."""

    def __init__(self, args, **kw):
        self.args, self.kw, self.code, self.terminated = args, kw, None, False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated, self.code = True, -15

    def wait(self, timeout=None):
        return self.code

    def kill(self):
        self.code = -9


@pytest.fixture
def made(tmp_path, monkeypatch):
    started = []

    def popen(args, **kw):
        started.append(Proc(args, **kw))
        return started[-1]

    monkeypatch.setattr(sup, "daemon_status", lambda: {"state": "running"})
    monkeypatch.setattr(sup, "port_open", lambda port: port != 8443)
    monkeypatch.setattr(sup, "wifi_address", lambda: "192.168.1.20")
    s = sup.Supervisor(logs=tmp_path / "logs", settings_path=tmp_path / "app.json", python="/venv/python", popen=popen,
                       environ={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}, shell_key=lambda: "key-from-shell", shell_notes_key=lambda: "notes-from-shell")
    g = s.gateway_module
    for name in ("HEADSETS", "PAIRING_CODE", "REMOTE_STATE"):
        monkeypatch.setattr(g, name, tmp_path / getattr(g, name).name)
    g.load_headsets.__defaults__ = (g.HEADSETS,)
    g.save_headsets.__defaults__ = (g.HEADSETS,)
    g.shown_code.__defaults__ = (g.PAIRING_CODE, g.shown_code.__defaults__[1])
    g.shown_remote.__defaults__ = (g.REMOTE_STATE,)
    return s, started, tmp_path


def test_connected_headsets_counts_open_lines_per_name():
    log = "\n".join([
        "gateway 0.0.0.0:8790, 2 headset(s) known",
        "192.168.1.7 samsung SM-I610 aba7 signalling: connected",
        "192.168.1.7 samsung SM-I610 aba7 daemon: connected",
        "192.168.1.7 samsung SM-I610 aba7 signalling: closed",
        "remote Meta Quest 3 51c0 backend: connected",
        "remote Meta Quest 3 51c0 backend: closed",
        "192.168.1.9: refused, no known token (GET /daemon/api)",
        "remote: reachable from other networks at wss://x.trycloudflare.com",
        "192.168.1.7: paired as someone",
    ])
    assert sup.connected_headsets(log) == ["samsung SM-I610 aba7"]
    assert sup.connected_headsets("") == []


def test_faces_line_is_the_last_one():
    log = ("INFO vision: Vision: 18.8 detections/s, 0.0 faces per frame, brightness 27/255\n"
           "INFO vision: Vision: 18.8 detections/s, 1.1 faces per frame, brightness 26/255\n")
    assert sup.faces_in_view(log) == "1.1 faces in view"
    assert sup.faces_in_view("nothing yet") == ""


def test_start_runs_gateway_and_backend_with_what_the_finder_does_not_give(made):
    s, started, tmp = made
    s.start()
    gateway, backend = started
    assert gateway.args[:2] == ["/venv/python", "-u"] and gateway.args[2].endswith("robot/scripts/gateway.py")
    assert "--remote" not in gateway.args
    assert backend.args == ["/venv/python", "-m", "reachy_meetings_backend.captions"]
    assert backend.kw["cwd"].endswith("/backend") and backend.kw["env"]["PYTHONPATH"] == "src"
    assert "/opt/homebrew/bin" in gateway.kw["env"]["PATH"]            # cloudflared
    assert backend.kw["env"]["DEEPL_AUTH_KEY"] == "key-from-shell"     # ~/.zshrc is not read for an app
    assert "DEEPL_AUTH_KEY" not in gateway.kw["env"]
    assert (tmp / "logs" / "gateway.log").exists() and (tmp / "logs" / "backend.log").exists()
    assert not (tmp / "app.json").exists() or "key-from-shell" not in (tmp / "app.json").read_text()


def test_a_key_already_in_the_environment_is_not_asked_for_again(tmp_path):
    asked = []
    s = sup.Supervisor(logs=tmp_path, settings_path=tmp_path / "app.json", popen=Proc,
                       environ={"DEEPL_AUTH_KEY": "from-terminal"}, shell_key=lambda: asked.append(1) or "other", shell_notes_key=lambda: "")
    s.start_backend()
    assert s.backend.kw["env"]["DEEPL_AUTH_KEY"] == "from-terminal" and asked == []


def test_remote_switch_is_remembered_and_restarts_only_the_gateway(made):
    s, started, tmp = made
    s.start()
    gateway, backend = started
    s.set_remote(True)
    assert json.loads((tmp / "app.json").read_text()) == {"remote": True}
    assert gateway.terminated and not backend.terminated
    assert len(started) == 3 and "--remote" in started[2].args
    s.set_remote(True)                      # no change: nothing restarts
    assert len(started) == 3
    again = sup.Supervisor(logs=tmp / "logs", settings_path=tmp / "app.json", popen=Proc, environ={})
    assert again.remote is True
    s.set_remote(False)
    assert "--remote" not in started[3].args


def test_meeting_language_is_remembered_and_restarts_only_the_backend(made):
    s, started, tmp = made
    s.start()
    gateway, backend = started
    assert "--meeting-lang" not in backend.args and s.status().meeting_lang == "auto"
    s.set_meeting_lang("de")
    assert json.loads((tmp / "app.json").read_text()) == {"meeting_lang": "de"}
    assert backend.terminated and not gateway.terminated
    assert started[2].args[-2:] == ["--meeting-lang", "de"] and s.status().meeting_lang == "de"
    s.set_meeting_lang("de")                # no change: nothing restarts
    s.set_meeting_lang("klingon")           # not offered: ignored
    assert len(started) == 3
    again = sup.Supervisor(logs=tmp / "logs", settings_path=tmp / "app.json", popen=Proc, environ={})
    assert again.meeting_lang == "de"
    s.set_meeting_lang("auto")
    assert "--meeting-lang" not in started[3].args


def test_stop_ends_what_it_started_and_nothing_else(made):
    s, started, _ = made
    s.start()
    s.stop()
    assert all(p.terminated for p in started) and s.gateway is None and s.backend is None
    s.stop()                                # twice is fine


def test_status_reads_what_the_gateway_wrote(made):
    s, started, tmp = made
    g = s.gateway_module
    s.start()
    token = g.add_headset("samsung SM-I610 aba7", g.HEADSETS)
    g.add_headset("Meta Quest 3 51c0", g.HEADSETS)
    g.Pairing(g.HEADSETS, g.PAIRING_CODE).current()
    (tmp / "logs" / "gateway.log").write_text("remote: the tunnel ended (1), starting it again\n"
                                              "10.0.0.7 samsung SM-I610 aba7 daemon: connected\n")
    (tmp / "logs" / "backend.log").write_text("Vision: 18 detections/s, 2.0 faces per frame, brightness 90/255\n")
    st = s.status()
    assert st.robot == "running" and st.backend and st.door and not st.camera
    assert st.known == ["Meta Quest 3 51c0", "samsung SM-I610 aba7"] and st.connected == ["samsung SM-I610 aba7"]
    assert len(st.code) == 7 and st.code[3] == " " and st.faces == "2.0 faces in view"
    assert st.address == "192.168.1.20" and st.translation_key
    assert not st.remote_wanted and st.remote_host == "" and "tunnel ended" in st.remote_note
    assert st.problem == "" and token not in repr(st)


def test_a_part_that_stopped_is_named_with_its_last_lines(made):
    s, started, tmp = made
    s.start()
    (tmp / "logs" / "backend.log").write_text("loading models\nRuntimeError: no microphone\n")
    started[1].code = 1
    st = s.status()
    assert st.problem.startswith("The backend stopped.") and "no microphone" in st.problem


def test_remove_headset(made):
    s, _, _ = made
    g = s.gateway_module
    g.add_headset("keep", g.HEADSETS)
    g.add_headset("go", g.HEADSETS)
    s.remove_headset("go")
    s.remove_headset("never there")
    assert list(g.load_headsets()) == ["keep"]


def test_robot_is_starting_only_after_we_opened_its_app(made, monkeypatch):
    s, _, _ = made
    monkeypatch.setattr(sup, "daemon_status", lambda: {})
    assert s.status().robot == ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
    s.open_control()
    assert s.opened_control and s.status().robot == "starting"


# ---------------------------------------------------------------- the app with its own Python
from reachy_meetings_app import libraries  # noqa: E402


def test_libs_folder_is_named_after_the_list(tmp_path):
    a = libraries.libs_dir("torch==2.14.1\nnumpy==2.5.4\n", tmp_path)
    assert a == libraries.libs_dir("# a comment\n\ntorch==2.14.1\n  numpy==2.5.4  \n", tmp_path)   # same list
    assert a != libraries.libs_dir("torch==2.14.2\nnumpy==2.5.4\n", tmp_path)
    assert a.parent == tmp_path and a.name.startswith("libs-")


def test_progress_follows_pips_words():
    p = libraries.Progress(4)
    p.line("Collecting torch==2.14.1 (from -r requirements-backend.txt (line 70))")
    assert p.text == "Fetching torch (1 of 4)" and 0.2 < p.fraction < 0.25
    p.line("  Downloading torch-2.14.1-cp312-none-macosx_11_0_arm64.whl (554.0 MB)")
    assert p.text == "Downloading torch (554.0 MB), 1 of 4"
    for name in ("numpy==2.5.4", "mlx==0.32.4", "deepl==1.32.0", "torch==2.14.1"):
        p.line(f"Collecting {name}")
    assert p.fraction == pytest.approx(0.9)          # a name seen twice counts once; unpacking is still to come
    p.line("Installing collected packages: numpy, torch")
    assert p.fraction > 0.9 and "Unpacking" in p.text
    p.line("Successfully installed numpy-2.5.4 torch-2.14.1")
    assert p.fraction == 1.0


class Pip(Proc):
    """Stands in for pip: says what pip says, and ends with the given code."""

    def __init__(self, args, code=0, **kw):
        super().__init__(args, **kw)
        self.stdout = iter(["Collecting numpy==2.5.4\n", "Collecting torch==2.14.1\n",
                            "ERROR: No matching distribution found for torch==2.14.1\n" if code else
                            "Successfully installed numpy-2.5.4 torch-2.14.1\n"])
        self.code_at_end = code

    def wait(self, timeout=None):
        return self.code_at_end


def test_install_marks_the_folder_complete_and_clears_older_ones(tmp_path):
    requirements = tmp_path / "requirements-backend.txt"
    requirements.write_text("numpy==2.5.4\ntorch==2.14.1\n")
    old = tmp_path / "support" / "libs-000000000000"
    old.mkdir(parents=True)
    folder = libraries.libs_dir(requirements.read_text(), tmp_path / "support")
    seen, calls = [], []

    def popen(args, **kw):
        calls.append(args)
        return Pip(args, **kw)

    libraries.install(requirements, folder, lambda p: seen.append((p.fraction, p.text)), "/app/python", popen)
    assert libraries.complete(folder) and not old.exists()
    assert calls[0][:6] == ["/app/python", "-m", "pip", "install", "--target", str(folder)]
    assert "--only-binary" in calls[0]                       # never builds from source on the user's Mac
    assert seen[-1] == (1.0, "Done") and seen[0][0] == 0.0
    assert "Collecting numpy" in (tmp_path / "support" / "setup.log").read_text()


def test_failed_install_is_not_complete_and_says_why(tmp_path):
    requirements = tmp_path / "requirements-backend.txt"
    requirements.write_text("numpy==2.5.4\ntorch==2.14.1\n")
    folder = libraries.libs_dir(requirements.read_text(), tmp_path / "support")
    with pytest.raises(RuntimeError) as failed:
        libraries.install(requirements, folder, python="/app/python", popen=lambda args, **kw: Pip(args, code=1, **kw))
    assert "No matching distribution" in str(failed.value) and not libraries.complete(folder)


def standalone_supervisor(tmp_path, started, pip_code=0):
    repo = tmp_path / "code"
    (repo / "laptop-app").mkdir(parents=True)
    (repo / "robot" / "scripts").mkdir(parents=True)
    (repo / "robot" / "scripts" / "gateway.py").write_text((sup.REPO / "robot" / "scripts" / "gateway.py").read_text())
    (repo / "laptop-app" / "requirements-backend.txt").write_text("numpy==2.5.4\n")

    def popen(args, **kw):
        started.append(Pip(args, code=pip_code, **kw) if "pip" in args else Proc(args, **kw))
        return started[-1]

    s = sup.Supervisor(repo=repo, logs=tmp_path / "logs", settings_path=tmp_path / "app.json", python="/app/python",
                       popen=popen, shell_key=lambda: "", shell_notes_key=lambda: "",
                       environ={"PATH": "/usr/bin", "REACHY_STANDALONE": "1", "REACHY_BUNDLE_BIN": "/app/bin"})
    s.support = tmp_path / "support"
    return s


def test_first_start_fetches_the_libraries_then_runs_the_backend_with_them(tmp_path, monkeypatch):
    monkeypatch.setattr(sup, "daemon_status", lambda: {"state": "running"})
    started = []
    s = standalone_supervisor(tmp_path, started)
    s.start()
    gateway, pip, backend = started
    assert "gateway.py" in gateway.args[2] and "pip" in pip.args      # the door is open while the libraries come
    assert gateway.kw["env"]["PATH"].startswith("/app/bin:")           # the app's own cloudflared first
    assert backend.kw["env"]["PYTHONPATH"] == f"src:{s.libs()}"
    assert s.libs().parent == tmp_path / "support" and libraries.complete(s.libs())
    started.clear()
    s.stop()
    s.start()                                                          # the second start: nothing to fetch
    assert [("pip" in p.args) for p in started] == [False, False]


def test_failed_setup_starts_no_backend_and_shows_why(tmp_path, monkeypatch):
    monkeypatch.setattr(sup, "daemon_status", lambda: {"state": "running"})
    monkeypatch.setattr(sup, "port_open", lambda port: False)
    monkeypatch.setattr(sup, "wifi_address", lambda: "")
    started = []
    s = standalone_supervisor(tmp_path, started, pip_code=1)
    s.start()
    assert len(started) == 2 and s.backend is None
    assert "could not be installed" in s.status().problem and s.status().setup == ""


def test_translation_key_set_in_the_app_wins_and_restarts_the_backend(made):
    s, started, tmp = made
    s.start()
    s.set_deepl_key("  key-from-the-app \n")
    assert started[1].terminated and started[2].kw["env"]["DEEPL_AUTH_KEY"] == "key-from-the-app"
    assert json.loads((tmp / "app.json").read_text())["deepl_key"] == "key-from-the-app"
    assert (tmp / "app.json").stat().st_mode & 0o777 == 0o600
    s.set_deepl_key("")                                   # taken out again: back to the one from the shell
    assert "deepl_key" not in json.loads((tmp / "app.json").read_text())
    assert started[3].kw["env"]["DEEPL_AUTH_KEY"] == "key-from-shell"
    assert "key-from-the-app" not in repr(s.status())


def test_sharing_the_screen_starts_and_stops_its_sender(made):
    s, started, tmp = made
    python = tmp / "control-python"
    python.write_text("")
    s.set_sharing(True, python)
    assert started[-1].args == [str(python), "-u", str(s.repo / "robot" / "scripts" / "share_screen.py")]
    assert s.status().sharing
    s.set_sharing(False)
    assert started[-1].terminated and not s.status().sharing


def test_sharing_without_reachy_mini_control_says_so(made):
    s, started, tmp = made
    s.set_sharing(True, tmp / "nothing-here")
    status = s.status()
    assert not started and not status.sharing and "Reachy Mini Control" in status.share_note


def test_a_sender_that_stopped_is_reported_once_with_its_last_words(made):
    s, started, tmp = made
    python = tmp / "control-python"
    python.write_text("")
    s.set_sharing(True, python)
    (s.logs / "screen.log").write_text("no picture of the screen: allow Screen Recording\n")
    started[-1].code = 1
    status = s.status()
    assert not status.sharing and "Screen Recording" in status.share_note
    assert s.screen is None and s.status().share_note == status.share_note   # still shown, not started again


def test_stop_also_ends_a_shared_screen(made):
    s, started, tmp = made
    python = tmp / "control-python"
    python.write_text("")
    s.set_sharing(True, python)
    s.stop()
    assert started[-1].terminated and s.screen is None


def test_a_chosen_window_is_shared_through_screencap_with_its_name(made):
    s, started, tmp = made
    python, helper = tmp / "control-python", tmp / "screencap"
    python.write_text("")
    helper.write_text("")
    s.environ["REACHY_SCREENCAP"] = str(helper)
    s.set_sharing(True, python, source="window:4711", label="Safari: Budget")
    assert started[-1].args[-6:] == ["--source", "window:4711", "--helper", str(helper), "--thumb", str(s.thumb)]
    status = s.status()
    assert status.sharing and status.share_label == "Safari: Budget" and status.share_thumb == ""
    s.thumb.write_bytes(b"jpeg")
    assert s.status().share_thumb == str(s.thumb)
    s.set_sharing(False)
    assert not s.thumb.exists() and s.status().share_label == ""


def test_without_screencap_the_whole_screen_is_shared(made):
    s, started, tmp = made
    python = tmp / "control-python"
    python.write_text("")
    s.screencap = lambda: None
    s.set_sharing(True, python, source="window:4711", label="Safari: Budget")
    assert "--source" not in started[-1].args
    assert s.status().share_label == "The whole screen"


def test_the_notes_key_comes_from_the_login_shell_too(made, tmp_path):
    s, started, tmp = made
    s.start()
    assert s.backend.kw["env"]["GEMINI_API_KEY"] == "notes-from-shell" and s.status().notes_key
    none = sup.Supervisor(logs=tmp_path / "l", settings_path=tmp_path / "a.json", popen=Proc, environ={},
                          shell_key=lambda: "", shell_notes_key=lambda: "")
    none.start_backend()
    assert "GEMINI_API_KEY" not in none.backend.kw["env"] and not none.has_notes_key


def test_with_the_hosted_service_the_backend_gets_an_installation_id_and_no_keys(tmp_path):
    s = sup.Supervisor(logs=tmp_path / "l", settings_path=tmp_path / "a.json", popen=Proc,
                       environ={"REACHY_SERVICE_URL": "https://example.test/meeting", "DEEPL_AUTH_KEY": "own"},
                       shell_key=lambda: "shell", shell_notes_key=lambda: "shell")
    s.start_backend()
    env = s.backend.kw["env"]
    assert env["REACHY_SERVICE_URL"] == "https://example.test/meeting" and "DEEPL_AUTH_KEY" not in env
    install = env["REACHY_INSTALL_ID"]
    assert len(install) == 36 and json.loads((tmp_path / "a.json").read_text())["install"] == install
    assert s.has_key and s.has_notes_key
    again = sup.Supervisor(logs=tmp_path / "l", settings_path=tmp_path / "a.json", popen=Proc,
                           environ={"REACHY_SERVICE_URL": "https://example.test/meeting"})
    assert again.install_id() == install   # the same installation after a restart


# ---------------------------------------------------------------- what the window says in plain words
def test_headsets_are_called_by_their_model_and_keep_their_digits_only_when_two_read_the_same():
    assert sup.friendly_names(["samsung SM-I610 aba7", "Meta Quest 3 51c0"]) == {
        "samsung SM-I610 aba7": "Galaxy XR", "Meta Quest 3 51c0": "Meta Quest 3"}
    assert sup.friendly_names(["samsung SM-I610 aba7", "samsung SM-I610 01ff", "kitchen"]) == {
        "samsung SM-I610 aba7": "Galaxy XR aba7", "samsung SM-I610 01ff": "Galaxy XR 01ff", "kitchen": "kitchen"}


def test_a_part_that_stopped_is_named_and_starts_again_when_asked(made):
    s, started, tmp = made
    s.start()
    started[1].code = 1
    assert s.status().stopped == "backend"
    s.restart("backend")
    assert len(started) == 3 and "reachy_meetings_backend.captions" in started[2].args and s.status().stopped == ""
    started[0].code = 1
    assert s.status().stopped == "gateway"
    s.restart("gateway")
    assert "gateway.py" in started[3].args[2] and s.status().stopped == ""


def test_a_failed_setup_can_be_tried_again(tmp_path, monkeypatch):
    monkeypatch.setattr(sup, "daemon_status", lambda: {"state": "running"})
    monkeypatch.setattr(sup, "port_open", lambda port: False)
    monkeypatch.setattr(sup, "wifi_address", lambda: "")
    started, codes = [], [1, 0]

    s = standalone_supervisor(tmp_path, started)
    first = s.popen
    s.popen = lambda args, **kw: (started.append(Pip(args, code=codes.pop(0), **kw)) or started[-1]) if "pip" in args \
        else first(args, **kw)
    s.start()
    assert s.status().setup_failed and s.backend is None
    s.retry_setup()
    assert not s.status().setup_failed and s.status().problem == "" and s.backend is not None


def test_fix_puts_the_video_patch_back_and_asks_for_a_restart_of_reachy_mini_control(made, monkeypatch):
    s, _, tmp = made
    ran = []

    def run(args, **kw):
        ran.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", run)
    s.fix_video()
    assert ran[-1] == ["/venv/python", str(sup.REPO / "robot" / "scripts" / "patch_daemon_video.py")]
    assert s.status().video_note == "restart"
    monkeypatch.setattr(sup, "daemon_status", lambda: {})       # Reachy Mini Control was quit: nothing left to say
    assert s.status().video_note == ""


def test_a_patch_that_does_not_fit_is_said_and_changes_nothing(made, monkeypatch):
    s, _, _ = made
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(args, 1))
    s.fix_video()
    assert s.status().video_note == "failed"
