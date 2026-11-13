# Reachy Meetings on the laptop (the Mac app)

One window instead of `./start_meeting.sh` in a terminal: it starts what the meeting needs on the laptop next to
Reachy, shows whether each part is up, and stops it again when you quit.

```bash
laptop-app/build_app.sh --standalone   # dist/standalone/Reachy Meetings.app: for any Apple Silicon Mac (120 MB)
laptop-app/build_app.sh                # dist/Reachy Meetings.app: a thin shell around this checkout, for development
```

Open *Reachy Meetings* from there, or drag it to Applications first. The window shows:

- **the parts** with a dot each: robot (Reachy Mini Control is opened if it does not answer), camera stream,
  captions and translation, the door for headsets, and which headsets are connected;
- **Other networks**: a switch for headsets at home (the same as `--remote`: a Cloudflare quick tunnel; remembered
  for the next start). Switching restarts the door; headsets reconnect by themselves;
- **the pairing code** for a new headset, large;
- **the headsets that may connect**, each with *Remove*;
- in orange, **what needs a look**: a part that stopped (with its last log lines), no DeepL key, or the
  sharper-video patch missing after an update of Reachy Mini Control.

Quitting the app (or closing its window) stops the door and the backend. Reachy Mini Control keeps running.

The first time, macOS asks whether *Reachy Meetings* may use the microphone (room sound and captions), the camera
(faces) and the local network (headsets on the Wi-Fi). Only one of the two can run at a time: the app or
`./start_meeting.sh`.

## The two builds

**Standalone** (`--standalone`) is the one to give to someone. It brings its own Python and our code (gateway, backend,
this app) and, if the Mac that builds it has it, `cloudflared` for the *Other networks* switch. It does not bring the
backend's libraries (speech recognition, faces, voices: about 1.6 GB): frozen into an app, those are where bundling
goes wrong, and every change of ours would be a 2 GB download. Instead:

- **On its first start** the app fetches exactly the versions in `requirements-backend.txt` with its own pip into
  `~/Library/Application Support/Reachy Meetings/libs-<name of the list>` (`reachy_meetings_app/libraries.py`), with a
  progress bar in the window. The door for headsets is open meanwhile, so a headset can already be paired. Pip's
  words are in `setup.log` next to that folder.
- **The models** (about 2 GB more) are fetched by the backend itself when it first needs them, as always; the
  *Captions* row says *starting …* until they are there.
- **A new list** (a build with other versions) means a new folder: the libraries are fetched again and the old ones
  removed.
- `requirements-backend.txt` is the list of the backend's environment when it was last known to work
  (`uv pip freeze`, see `build_app.sh`); the versions are for Apple Silicon.

What the other Mac still needs: **Reachy Mini Control** (Pollen's app, for the robot itself), and for translation a
DeepL key, set under *Reachy Meetings > Translation Key…* in the menu bar (kept in the app's settings, readable by
this user only). The app is signed only locally: on another Mac, macOS will refuse the first open until the user
allows it under System Settings > Privacy & Security.

**The thin shell** (no flag) runs `reachy_meetings_app` from this checkout with the backend's Python
(`~/.venvs/reachy-backend`). A `git pull` updates it without building again. macOS also asks for the folder the
checkout is in, when that is Documents, Desktop or Downloads.

Both need the Xcode command line tools to build. The icon is the project's (`xr-client/pages/icon.svg`), made into
`AppIcon.icns` by `make_icon.py`.

## Updates (the standalone app)

The app updates **our code** by itself: this app, the gateway and the backend, a package of about 70 kB. Python and
the app's program stay as installed, and the backend's libraries follow when a new code brings another list.

- **For the user:** the app looks for a newer version at every start and every six hours, fetches it, and says
  *Version x is ready. It is used from the next start.* with a *Restart now* button. The window title shows the
  version that runs. Nothing to do otherwise.
- **To publish one:** `laptop-app/release.sh 0.3.0` makes three files in `dist/release` (`latest.json`, the package,
  its signature) and prints the `gh release create` line that publishes them as a GitHub release of this
  repository. The apps look at the latest release (`reachy_meetings_app/updates.py`). `VERSION` is the version an
  app is built with; an update must have a higher one.
- **Only ours:** an app takes a package only if it is signed with the release key, says the announced version
  inside, and is newer than what runs. The key is `~/.config/reachy-meetings/release_key.pem` on the Mac that
  publishes (made on 2026-11-13 on Kevin's; **keep a copy somewhere safe and keep it private**: without it no
  installed app takes another update). Its public half, `release_key.pub.pem`, is in the repository and in every app.
- **If an update is broken:** a version that does not get as far as its window is not started a second time; the
  app goes back to the code it was installed with (`updates.choose`, asked by `launcher.c`) and does not fetch
  that version again. `~/Library/Logs/reachy-meetings/update.log` says what the app found.
- **Not updated this way:** the app's program and its Python (a new build, by hand), and Reachy Mini Control.
- `python -m reachy_meetings_app --version` (or the app's program with `--version`) prints the version and where
  the running code is: inside the app, or in `~/Library/Application Support/Reachy Meetings/code/<version>`.

- `launcher.c`: the app's own program, with Python running inside it. A shell script in its place does not do:
  macOS decides by the app's program who is asking for microphone, camera, local network or a folder in Documents,
  and with a script it never asked and just refused (the first version could not read this checkout in Documents
  and vanished). Gateway and backend are started by the app, so they come under the same answers.

- `reachy_meetings_app/supervisor.py`: starts and watches the gateway (`robot/scripts/gateway.py`) and the backend,
  and gathers the status. No window in it; tests in `tests/` (`pytest -q` with the backend's Python).
- `reachy_meetings_app/window.py`: the window, native (AppKit through pyobjc, already in the backend's environment).
- The DeepL key: the one set in the app counts. Without one, the supervisor asks a login shell for `DEEPL_AUTH_KEY`
  once (an app opened from the Finder does not read `~/.zshrc`) and hands it to the backend without writing it down.
- Logs as before: `~/Library/Logs/reachy-meetings/gateway.log` and `backend.log`.

To look at the window without starting anything: `python -m reachy_meetings_app --snapshot window.png` (add `--demo`
for made-up values). The picture has no background; lay it on a dark or light one.
