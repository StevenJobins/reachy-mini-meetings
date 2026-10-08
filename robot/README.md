# robot – Reachy Mini Lite side

Python package `reachy_meetings_robot`. Turns the Reachy Mini Lite into the remote user's physical avatar:

- **Head mirroring**: the headset pose drives the robot head, and the body turns along when needed.
- **Gestures**: nod, shake, tilt, antenna emotions. They are layered *on top of* the mirroring.
- **Camera**: frames are stamped with the head pose, which the xr-client needs for reprojection.
- **Microphone + DoA** (direction of arrival): audio goes to the backend for speech-to-text and translation. The speaker direction is used for the speech bubbles.
- **Speaker**: plays the remote user's translated speech, and the antennas move while it talks.
- **Bridge**: a WebSocket server that the xr-client and the backend connect to.

## Setup (macOS, Lite over USB)

Follows the [Pollen install guide](https://huggingface.co/docs/reachy_mini/main/SDK/installation). It uses Python 3.10–3.12 and `uv`, not conda base.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh      # once
cd robot
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -e ".[dev]"            # installs reachy-mini + this package
uv pip install "reachy-mini[mujoco]"  # optional: simulation
```

## Run

```bash
# Terminal 1 – daemon (talks to the robot over USB, serves http://localhost:8000)
reachy-mini-daemon            # real robot
reachy-mini-daemon --sim      # simulation, no robot needed

# Terminal 2
python scripts/check_connection.py     # smoke test: wake up, nod, antennas, sleep
python scripts/demo_gestures.py        # every gesture once
python scripts/axis_check.py           # real robot: do yaw/pitch/roll go the documented way?
reachy-meetings --no-media             # avatar: motion + bridge on ws://0.0.0.0:8765

# Terminal 3 – fake headset until the xr-client exists
python scripts/mock_headset.py
```

Tests, without robot or daemon: `pytest -q`

## Camera calibration (`scripts/calibrate_camera.py`)

Calibrates exactly the stream the headset sees, for the VR video sphere and the face directions:

1. Wake Reachy up on the page in a desktop browser; open *Settings → Checkerboard* on a tablet or second screen.
2. Hold the board in front of Reachy at many angles and distances, also in the corners and at the edges of the picture, and press *Capture calibration frame* (or *Auto-capture every 2 s*). 30-40 frames with the whole board visible.
3. `pip install opencv-python numpy`, then `python scripts/calibrate_camera.py ~/Downloads/reachy-calib-*.png`
4. It writes `xr-client/pages/camera.json` and warns about too few frames or uncovered regions. Commit it; the page uses it automatically.

Why 30+ frames: simulated with this lens, the corner fit is ~0.2 px either way, but the error over the whole picture was 6-21 px with 16 frames and ~2.6 px with 30-40 well spread frames.

## Layout

```
src/reachy_meetings_robot/
├── main.py              Avatar (wires everything) + CLI + ReachyMiniApp for the dashboard
├── default_config.yaml  all tunables (rates, smoothing, limits, ports); override with --config
├── config.py            YAML -> dataclasses
├── connection.py        connect / wake_up / goto_sleep
├── safety.py            clamp to limits, body follow
├── state.py             shared RobotState (what was commanded, speaking, DoA …)
├── motion/
│   ├── controller.py    THE control loop: the only place that calls set_target()
│   ├── head_mirror.py   headset pose -> smoothed head target
│   └── gestures.py      gesture library (offset functions) + player
├── media/
│   ├── camera.py        pose-stamped frames
│   ├── audio_in.py      mic chunks + DoA
│   └── audio_out.py     TTS playback queue
└── bridge/
    ├── protocol.py      JSON messages (keep in sync with xr-client / backend)
    └── server.py        WebSocket server
scripts/                 check_connection, demo_gestures, mock_headset
tests/                   pure-logic tests
```

## Design rules

1. **Only `MotionController` moves the robot.** Everything else sends it intents (a head pose or a gesture). Otherwise the `set_target` / `goto_target` calls fight each other.
2. **Gestures are offsets, not poses.** A gesture is `f(s) -> (roll, pitch, yaw, antL, antR)` in degrees, with `s ∈ [0, 1]`. It starts and ends at 0. To add one, write the function and register it in `GESTURES`.
3. **Report what was commanded.** `RobotState` holds the head pose after clamping. The headset reprojects against exactly that.
4. **Units:** everything internal is in degrees and mm. Conversion to the SDK (4×4 pose matrix, radians) happens only in `controller.tick()`.

## SDK facts used (reachy-mini 1.11, checked against the source)

- `set_target(head=4x4, antennas=[right, left] rad, body_yaw=rad)`. **The antenna order is right, left.**
- `create_head_pose(x, y, z, roll, pitch, yaw, mm=False, degrees=True)`
- Limits: head pitch/roll ±40°, head yaw ±180°, body yaw ±160°, max 65° between head and body yaw
- Audio: float32, 16 kHz. `push_audio_sample` is non-blocking. `get_DoA()` returns `(rad, speech)`, where 0 = left, π/2 = front, π = right.
- `media.get_frame()` returns a (H, W, 3) uint8 array, and `media.get_frame_jpeg()` exists too.

## Sharper video: `scripts/patch_daemon_video.py`

The daemon gives the camera (1080p60) to webrtcsink without settings: start 2 Mbit/s, max 8, and below 2 Mbit/s webrtcsink scales the picture down to 720p/360p, which looked washed out in the headset. There is no daemon option, so the script patches `media_server.py` of the installed desktop app (backup `media_server.py.orig`): bitrate 3–15 Mbit/s (start 6). `--fps 30` adds a `videorate` to 30 fps in the WebRTC branch, but **don't use it**: with it, every headset session hung "stuck mid-negotiation" (daemon log: `signaling_state have-local-offer`, ICE `new`) about a minute after connecting, 4 times in 6 minutes, none with the bitrate-only patch. Why it was tried: with full 1080p60 arriving, VR dropped to ~19 fps (debug panel: video rx 60 fps, tex 19 fps, send 19 Hz); the page now limits the texture uploads to 30/s instead (`videosource.js`). The script always starts from the backup, so running it again with other options replaces the old patch. `--h264` also forces H264 (Apple hardware encoder `vtenc_h264_hw` is available; VP8 runs in software at its fastest/lowest setting). `--revert` restores the original. Restart the daemon afterwards (quit and reopen Reachy Mini Control); run it again after an app update. Check the result in the VR debug panel (incoming resolution, fps, bitrate, codec).

## Open TODOs

- [ ] **Verify the axis signs on the real robot** (roll/pitch/yaw, antennas), then fix the comment in `bridge/protocol.py`
- [ ] Video transport to the headset: the daemon's WebRTC stream or our own JPEG/H.264 over WebSocket, plus pose metadata (`media/camera.py`)
- [ ] Mic audio → backend (STT/translation): wire it up in `main.py`
- [ ] Measure latency: headset `t` vs. receive time, then add predictive filtering in `head_mirror.py`
- [ ] Glance at the speaker via DoA (optional, needs arbitration with mirroring)
- [ ] Tune gestures on the hardware (amplitudes, durations)


## `turn_to_speaker.py` – turn towards whoever is speaking

Uses the Direction of Arrival (DoA) of the Reachy Mini's ReSpeaker mic array (daemon endpoint `/api/state/doa`) to turn head and body towards the current speaker in one smooth motion.

- **Speaker tracking:** every speech reading updates the target; head and body turn together (max. 200°/s). Front/back cannot be distinguished by the mic array – a speaker behind the robot is reached by turning continuously.
- **"I want to speak" button:** press Enter in the terminal → Reachy turns to the centre of all speaker directions of the last 60 s (each 20° sector counts once, so everyone feels addressed), waves the right antenna and swings its body ±27° for 3 s.

```bash
# Reachy Mini Control App must be running (daemon on localhost:8000)
pip install reachy-mini numpy
python robot/turn_to_speaker.py   # Enter = I want to speak, Ctrl+C = stop
```
