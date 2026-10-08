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

## Status (newest first, one line each: date, who, what, branch)

- 2026-10-08 Dominic/Claude: bug sweep, "Translate me" latency (5-10 s) + language choice (what Reachy speaks,
  what the bubbles show), Depth Anything in the backend. Work on branches `claude/*`, merged to main after a
  test. Does not touch Simon's areas above.
