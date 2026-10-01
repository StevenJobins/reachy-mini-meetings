# XR client strategy: web app first, native app later

Decided on 2026-10-01.

## Decision

We build **all** features first as a WebXR web app (`xr-client/pages/`, hosted on GitHub Pages). We only turn it into an installable app once everything works.

Why:

- **No install step.** A headset opens one link, which works on Galaxy XR, Quest and in desktop Chrome for debugging.
- **Fast iteration.** A push to `main` is live in about 1 minute, with no APK build or sideloading.
- **The robot connection already exists.** Pollen's JS SDK (`@pollen-robotics/reachy-mini-sdk`) provides HF sign-in, central signalling, WebRTC video, `set_target` and the pose stream. A native app would have to reimplement all of this.
- **One problem at a time.** We do not debug the technology and the features at the same time.

## Rules for the web app

These keep the later conversion cheap.

1. **No framework.** Use plain ES modules from a CDN, with no build step.
2. **Logic is separate from rendering.** Pose maths, filters, the protocol and the robot connection live in their own modules with no three.js or DOM inside. These modules can be ported to C#/Kotlin line by line.
3. **Every convention is documented.** This covers axes, units, angle signs and message formats (see the axis table in `xr-client/README.md`). Nothing is implicit.
4. **The robot side does not care about the client.** `robot/` and `backend/` must not depend on how the headset app is built.
5. **We measure before we switch.** We decide on going native from numbers (motion-to-photon latency, fps, depth-inference time), not from gut feeling.

## Roadmap

| Step | What | Status |
|---|---|---|
| 1 | Local WebXR teleop over USB (`xr-client/web/`, adb reverse) | done |
| 2 | Hosted teleop (`xr-client/pages/`): HF sign-in, central signalling, world-anchored video at the measured head pose | done (video confirmed 2026-10-01) |
| 3 | One-tap start: PWA with a launcher icon, silent sign-in, auto-pick the single robot, one "Start" button | in testing |
| 4 | Real Reachy Mini Lite: verify the axis signs, set the real camera FOV, measure baseline latency (WP1) | |
| 5 | Features in the web app: depth reprojection (WP2), speaker attribution (WP3), speech bubbles (WP4) | |
| 6 | Package as an app: Trusted Web Activity (Bubblewrap). This produces an APK with the same code and Chrome inside, so WebXR still works | |
| 7 | Only if step 5 hits performance limits: a native app (Unity/OpenXR or Android XR), porting the logic modules | optional |

## What carries over to a native app

- **Unchanged:** the robot side (daemon, `robot/`, `backend/`) and the protocol (WebRTC through Pollen central, `set_target`, the pose channel).
- **Ported 1:1:** the maths (axis mapping, filters, reprojection).
- **Rewritten:** only rendering and UI.
