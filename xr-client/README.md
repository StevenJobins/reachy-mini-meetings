# xr-client

Headset app (Meta Quest / Galaxy XR): video stream, latency-hiding reprojection, speech bubbles, copilot panel.

## WebXR teleop (`web/index.html`)

Baseline teleop for debugging, no build step needed. It does two things:

- **Video:** it shows the daemon's WebRTC stream, using GStreamer signalling on port 8443. The image is a window anchored in the room, placed where the robot head actually looks. The measured head pose comes from the daemon state WebSocket on port 8000. The window size matches the camera's field of view (`?vfov=80` for the sim camera, at a distance of `?dist=3` m). When you turn your head, the image stays put until the robot has followed. This is rotation-only reprojection, the fallback from WP2. `?mode=head` brings back the old head-locked screen.
- **Head pose:** it sends the headset orientation to the robot bridge (`head_pose` on port 8765) at 50 Hz, relative to the recenter pose. To recenter, pinch or pull the trigger, or press the Recenter button.

A status panel below the video shows the signalling, ICE and bridge state, the send rate, and the sent angles next to the angles the robot actually commanded.

### Run (Galaxy XR over USB)

```bash
reachy-mini-daemon --sim                    # or the Reachy Mini Control app, or the real Lite
reachy-meetings --no-media                  # robot bridge, ws://0.0.0.0:8765 (see robot/README.md)
python3 -m http.server 8080 -d xr-client/web

adb reverse tcp:8080 tcp:8080               # page
adb reverse tcp:8443 tcp:8443               # WebRTC signalling
adb reverse tcp:8765 tcp:8765               # robot bridge
adb reverse tcp:8000 tcp:8000               # daemon state (measured head pose)
```

On the headset, open `http://localhost:8080` in Chrome and press **Enter VR**. Because everything is on `localhost`, the page counts as a secure context, so WebXR works without HTTPS.

- **Debugging:** open `chrome://inspect` on the Mac to see the headset tab's console and network.
- **Without XR:** open `http://localhost:8080` on the Mac to check the video and the bridge.
- **Overrides:** `?host=<ip>`, `?signalling=ws://…`, `?bridge=ws://…`, `?daemon=ws://…`, `?hz=30`, `?vfov=`, `?dist=`, `?mode=head`

**The video itself does not go through adb.** WebRTC media runs over UDP between the headset and the Mac, so the headset needs network access, for example the same Wi-Fi. If there is no direct route, the daemon also offers TURN relays.

### Axis convention

| | x | y | z |
|---|---|---|---|
| WebXR | right | up | back (-z = forward) |
| Robot | forward | left | up |

The quaternion is mapped as robot `(x, y, z) = (-xr.z, -xr.x, xr.y)`, followed by Euler `xyz` as in `create_head_pose`. Checked numerically:

- turn left 30° → yaw +30
- look down 20° → pitch +20
- tilt right 15° → roll +15

Still to do: verify the signs on the real robot (see the TODO in `robot/README.md`).

## Hosted teleop (`pages/index.html`)

URL: https://stevenjobins.github.io/reachy-mini-meetings/ (deployed by `.github/workflows/pages.yml` on every push to `main` that touches `xr-client/pages/`).

This version needs no cable, no certificate and no laptop server for the teleop itself. It uses Pollen's JS SDK `@pollen-robotics/reachy-mini-sdk@1.11.0`, loaded from jsdelivr, and goes through these steps:

1. Sign in with Hugging Face (OAuth).
2. Pollen's central signalling finds the robot. The daemon must be signed in to HF on the dashboard.
3. WebRTC carries video, `set_target` commands and the measured pose stream, directly between the headset and the robot.

Requirements and behaviour:

- **Who can connect:** only HF accounts that can see the robot in central signalling.
- **Pose pipeline:** the page does the same pose processing as `robot/` itself (EMA, limits, body follow), because the Python bridge is not in this path.
- **Setup:** `HF_CLIENT_ID` in `pages/index.html` must hold the client ID of a Hugging Face OAuth app whose redirect URL is the page URL above.
