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
