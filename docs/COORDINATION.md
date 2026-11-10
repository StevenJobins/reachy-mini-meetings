# Coordination between parallel work (people and their Claude sessions)

Two people work on this repo at the same time, each with a Claude Code session on their own machine. The
sessions cannot message each other, so this file is the shared channel. **Every session: read it after each
`git pull`, add a line under "Status" before starting something bigger, and don't edit areas owned by
someone else; propose changes here instead.**

## Ownership (who decides, who edits)

| Area | Owner | Files |
|---|---|---|
| VR view, reprojection (lens-corrected sphere, world-locked / comfort mode), depth deformation of the grid | Simon (WP2) | `xr-client/pages/scene.js` (view/reprojection parts), `camera.js`, `speaker.js` (mic-direction tracker), `robot/scripts/calibrate_camera.py` |
| Laugh gesture | Simon | laugh parts in `app.js`, `gestures.js` |
| Backend: captions, translation, room audio, tunnel, "Translate me" voice, faces (`vision.py`), depth estimation (`depth.py`, planned) | Dominic | `backend/**` |
| Speaker following / framing (who talks, where Reachy looks), face tracks | Dominic | `app.js` (frameFocus, onVadEvent, flushDoa), `speakers.js`, `faces.js` |
| VR dock / buttons, captions UI, notes, mic, room audio playback | Dominic | `scene.js` (dock, warning line), `captions.js`, `notes.js`, `mic.js`, `roomaudio.js` |
| Daemon video patch, camera calibration with the real camera | Dominic | `robot/scripts/patch_daemon_video.py`, `robot/scripts/calibrate_camera_apriltag.py` |

`xr-client/pages/camera.json` is **measured data** (real camera, AprilTag grid, 2026-10-08, see
`robot/README.md`). Don't overwrite it with a calibration from simulated frames; if a new real calibration is
better (lower RMS over more views), replace it and note the numbers here.

## Interfaces between the areas

- Faces: backend -> page `{"type": "faces", "people": [{cx, cy, top, w, h, mouth}], "t"}` (~20/s), see
  `backend/README.md`.
- Depth (planned, backend part by Dominic, page part by Simon): backend -> page
  `{"type": "depth", ...}`, format documented in `backend/README.md` once it exists. Meant for deforming the
  video sphere grid (`scene.js`), which Simon designed for it.

## Open notes for Simon

- ~~Code review 2026-10-08: merge leftover in `app.js` (`setViewMode` missing its `}`, camera-model / calibration wiring re-bound on every view toggle).~~ Fixed in e07fdd1 (Simon).

## Status (newest first, one line each: date, who, what, branch)

- 2026-11-10 Dominic: main merged into `unity-translate` (Kevin's subtitles, voice speakers, front-half turning, 9-stop scan, LAN relay). One mic path for both: `MicSender` is Kevin's tee to the backend, plus Translate me (WebRTC track off while it is on); `QuickMenu` sends `me_mode` (`speak` = Translate me) on every connection and on switching, after `RoomSound`'s default `false`. `BackendClient`: Kevin's queue and `onConnect`, plus `Connects` and dropping queued messages on a reconnect. `case "me"` feeds the "You" line and, with Translate me, the text under the menu. Scene regenerated with `ProjectConfigure`. Not yet tried in the headset: whether the subtitle panel and the menu column get in each other's way. Branch `unity-translate`.

- 2026-11-06 Dominic: pointing like the headset's menus: aim ray per hand with a dot and a closing pinch ring (`XrInput.Aim`, `Ui.Cursor`) on the menu and the notes; either hand picks, a right pinch on them does not talk (`RobotControl`); notes carried with either hand, never the head; menu off the video window, still in the room at the lower left. Branch `unity-translate`.
- 2026-11-06 Dominic: meeting notes per meeting (backend `summary.py`: new meeting after 20 min without speech or `{"type": "new_meeting"}`, `--meeting-gap`, two tests; Unity: *New meeting* on the notes card, confirm with a second pinch, card clears). Notes carried with the hand instead of the head. Menu moved in to 26°, 8° down. Branch `unity-translate`.
- 2026-11-06 Dominic: pinch not recognised in the tour: hand tracking permission and XR Hands joints as a second pinch source, eye gaze for picking (new `XrInput.cs`; OpenXR features Hand Tracking + Eye Gaze Interaction in `ProjectConfigure`; in `RobotControl` the pinch level is the larger of profile and joints). Menu moved in to 37°. Branch `unity-translate`.
- 2026-11-06 Dominic: after a headset test: menu as an always visible column at the left edge of the video with a language list (`QuickMenu.cs` rebuilt), interactive tour during which Reachy waits (`Tutorial.cs` rebuilt), notes card draggable by a handle (`NotesPanel.cs`), shared drawing helpers (`Ui.cs`). In Kevin's `RobotControl.cs`: pinch levels, `Recenter()`, pause while the tour is open, left pinch routed to notes handle / menu / tour first. Status text back at Kevin's position. Branch `unity-translate`.
- 2026-11-06 Dominic: Unity meeting notes (new `NotesPanel.cs`, card 45° right of straight ahead, from the backend's `summary`; `case "summary"` in `CaptionView`, wiring in `ProjectConfigure`; one more tour card). Branch `unity-translate`.
- 2026-11-06 Dominic: Unity translation and onboarding, touching Kevin's files only with small additions. Translate me over the backend (`MicSender.cs`, `BackendClient.SendBinary`); bubble language = headset language; settings row under the image, shown on gaze, chosen with look + left pinch (new `QuickMenu.cs`); guided tour on the first start (new `Tutorial.cs`); one branch in `RobotControl.HandlePress`, `case "me"` in `CaptionView`, wiring in `ProjectConfigure` (status text 0.1 m lower). Built, not yet tried in the headset. Branch `unity-translate`.
- 2026-10-30 Kevin/Claude: midterm slides (Simon's deck) replaced by a simple 4-slide version for the move to Unity (`xr-client/unity`): title with 3D robot, demo video, what we did and solved, timeline. Note for Dominic and Simon: the web app is now only a reference, and speaker following (Dominic's area) is out of the Unity app for now, see `xr-client/unity/README.md`.
- 2026-10-28 Kevin/Claude: midterm slides (Simon's deck) for Kevin presenting alone: speaker notes rewritten for one speaker (170 s), measured results on slide 1, 233 ms labelled as desktop baseline, blockers slide as 2 blockers + 2 lessons, "today" on the timeline. Fonts and three.js copied to `midterm/vendor/` (runs offline via a local server), `?print` mode and `midterm-slides.pdf` for the submission, presenter window (P, `?presenter`, synced over a BroadcastChannel) with cues and Q&A answers in the notes. Main.
- 2026-10-24 Simon/Claude: midterm slides, public at `xr-client/pages/midterm/index.html` (one HTML file; 3D model from `presentation/midterm/export_reachy_model.py`). Main.
- 2026-10-16 Simon/Claude: room scan showed one photo 12 times since the direct VideoFrame upload (be9e67f): `captureFrame` read `source.canvas`, which is no longer redrawn. Now `videosource.currentImage()` (the current VideoFrame in track mode). Kevin: keep this in mind if the canvas path changes again. Main.
- 2026-10-16 Simon/Claude: `captions.js` (your area): Simon's Mac tunnel key added to `TRUSTED_KEYS` (backend now also runs there, for the headset latency test). Main.
- 2026-10-16 Simon/Claude: `captions.js` (your area): `tunnelUrl` is now exported (one word), so `qr.html` finds the backend like the main page (localhost, else tunnel with the tab's HF sign-in). Settings gets a link "Open the QR page" (same tab, keeps the sign-in). Main.
- 2026-10-16 Simon/Claude: backend `captions.py` (your area): new client message `{"type": "time", "id"}` -> `{"type": "time", "id", "t": ms}` for clock sync (latency test with the QR on another screen, `xr-client/pages/qr.html`, `clocksync.js`). Needs a backend restart on the robot laptop. Main.
- 2026-10-16 Simon/Claude: camera latency test (new `latency.js`, Settings button in `index.html`, hold-still override in `app.js` next to the room scan). Main.
- 2026-10-16 Simon/Claude: page header gets a "Signed in · <HF account>" chip (`index.html`, chips in `app.js`); settings card: long debug lines and selects no longer stick out (grid items `min-width: 0`). Main.
- 2026-10-16 Simon/Claude: robot connect (`robot.js` connect(), `app.js` connect() message) — touched Dominic's area on request: `autoConnect` now lists busy robots too (`filterBusy: false`), so the page tells "busy (another device)" from "no robot visible for HF account X" (before, both showed "busy"); the signed-in HF account is shown in the status line. Main.
- 2026-10-08 Dominic/Claude: bug sweep, "Translate me" latency (5-10 s) + language choice (what Reachy speaks,
  what the bubbles show), Depth Anything in the backend. Work on branches `claude/*`, merged to main after a
  test. Does not touch Simon's areas above.
