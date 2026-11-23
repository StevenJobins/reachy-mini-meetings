# Reachy Meetings on the laptop (the Mac app)

One window instead of `./start_meeting.sh` in a terminal: it starts what the meeting needs on the laptop next to
Reachy, shows whether each part is up, and stops it again when you quit.

```bash
laptop-app/build_app.sh --standalone   # dist/standalone/Reachy Meetings.app: for any Apple Silicon Mac (120 MB)
laptop-app/build_app.sh                # dist/Reachy Meetings.app: a thin shell around this checkout, for development
```

Open *Reachy Meetings* from there, or drag it to Applications first. The window is laid out like a video meeting
(since 2026-11-16; dark, resizable):

- **the stage**: the person in the headset as a little Reachy with the headset's name (as people call it: *Galaxy
  XR*, not `samsung SM-I610 aba7`; `friendly_names` in `supervisor.py`), a green ring while they speak. With no
  headset connected it says what to do: put on the headset, open Reachy Meetings, enter the code (shown large). If a
  headset is paired already, it says that it connects by itself, and the code is smaller, for a new one;
- **the last caption** under it (what was said in the room, or by the headset user, and its translation);
- **a row of round buttons, each with its word under it**: *Share* with the headset: a menu as in a video call, a
  whole screen, all windows of one app, or one window (also while others lie on it); while shared, the stage shows
  *The headset sees: …* and a live picture of it, and the button stops it (`screencap.swift`, built into the app,
  captures with ScreenCaptureKit; `robot/scripts/share_screen.py` sends it; an app built before 2026-11-18 shares only
  the whole screen; macOS asks once for Screen Recording; needs Reachy Mini Control on this Mac, same Wi-Fi only),
  *Captions* on / off, *Remote* (other networks, below), *Headsets* (the side panel), *Notes* (the meeting notes as a
  draft in this Mac's mail program; the only place to send them from, the headset has no such button since
  2026-11-18), *Help* (what Reachy's gestures mean: a window with a little moving Reachy per gesture, `gestures.py`;
  it opens by itself the first time), *Key* (the translation key; hidden with the hosted service), and in red *End*
  (quits, stops the parts);
- **the side panel**, written for someone who does not know what a gateway or a backend is (since 2026-11-23):
  - **one line on top**: green *Everything is ready*, grey *Getting ready …*, orange *Needs a look*. Under it only
    what is not ready, in plain words, with a button where one helps: *Reachy does not answer* (*Open Reachy Mini
    Control*), *Captions stopped* / *Headsets cannot connect right now* (*Start again*: `Supervisor.restart`), the
    free hour used up (*More time*: the plans page), no translation key (*Set the key*), *The picture in the headset
    is less sharp than it could be* (*Fix*: runs `robot/scripts/patch_daemon_video.py`, then asks to quit and reopen
    Reachy Mini Control; if the patch does not fit that version, nothing is changed and the line goes away), and
    *An update is ready* (*Restart now*);
  - **Headsets**: the ones that may connect, each with *Remove*; *(not here)* behind one that is not connected;
  - **the code for another headset**, only while one is connected (before that it is on the stage);
  - **Language Reachy speaks**: the language Reachy says the headset user's words in, with *Translate me* on in the
    headset. *Auto* (the default) is the language most spoken in the room; the people in the room know it better, so
    set it here when it is clear. Remembered; changing it restarts the backend (captions pause a few seconds,
    headsets reconnect by themselves). Passed to the backend as `--meeting-lang`;
  - **Show details**, closed until asked for: what `start_meeting.sh` shows in the terminal. The parts with a dot each
    (robot, camera stream, captions and translation with the faces in view, the door for headsets with this Mac's
    address, other networks), the version, the installation's id at the hosted service, the last log lines of a part
    that stopped, and the commands for what the buttons above cannot do (the key for the notes).
- **Other networks**: the globe button (*Remote*), for headsets at home (the same as `--remote`: a Cloudflare quick
  tunnel; remembered for the next start). Switching restarts the door; headsets reconnect by themselves.

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
  large progress bar over the whole window (`Setup` in `window.py`: per cent only, no package names; then a moving bar
  while the backend fetches its models and starts for the first time; *Try again* if it fails, which is
  `Supervisor.retry_setup`). The door for headsets is open meanwhile. Pip's
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
- `reachy_meetings_app/window.py`: the window, native (AppKit through pyobjc, already in the backend's environment;
  Cocoa only, everything drawn in `drawRect`).
- `reachy_meetings_app/live.py`: listens to the backend's messages on `ws://127.0.0.1:8766` for the caption line and
  the speaking ring (standard library only; it never sends a setting).
- The DeepL key: the one set in the app counts. Without one, the supervisor asks a login shell for `DEEPL_AUTH_KEY`
  once (an app opened from the Finder does not read `~/.zshrc`) and hands it to the backend without writing it down.
- The key for the meeting notes: `GEMINI_API_KEY`, asked from the same login shell (so `export GEMINI_API_KEY="..."` in `~/.zshrc`; free key from aistudio.google.com/apikey). Without it there are no notes, and the side panel says so. Until 2026-11-18 the app did not look it up: a meeting started from the app never had notes.
- Logs as before: `~/Library/Logs/reachy-meetings/gateway.log` and `backend.log`, and `screen.log` while sharing.

To look at the window without starting anything: `python -m reachy_meetings_app --snapshot window.png` (add `--demo`
for a meeting with made-up values, `--demo-waiting` for a paired headset that is not there, `--demo-new` for none paired
yet, `--demo-setup` and `--demo-models` for the two parts of the first start, `--demo-problem` for what the side
panel says when parts stopped).
