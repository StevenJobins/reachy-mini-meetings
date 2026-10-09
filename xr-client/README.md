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

## Hosted teleop (`pages/`)

URL: https://stevenjobins.github.io/reachy-mini-meetings/. It is deployed by `.github/workflows/pages.yml` on every push to `main` that touches `xr-client/pages/`.

**Use on the headset:**

1. Open the URL in Chrome. Then choose ⋮ → *Install app* / *Add to home screen*, so the app gets an icon in the launcher.
2. Start the app. The first time, sign in with Hugging Face. After that, sign-in is silent (OAuth `prompt=none`).
3. The app connects to the robot automatically. When exactly one free robot is visible, it is picked without asking. The robot stays asleep.
4. Tap **Wake up**. Reachy plays its wake-up motion (~2 s) and its camera switches on. **Robot sound is muted by default** (otherwise you hear yourself twice); *Unmute robot* / *Mute robot* toggles it, on the page and in VR.
5. Look straight ahead and tap **Start**. You are in VR and Reachy follows your head. Leaving VR keeps Reachy awake, so you can tap Start again.
   - **VR buttons**: see *VR interface* below.
   - **Two-way audio** (`mic.js`): while Reachy is awake, your microphone goes to the robot speaker, like a video call: the headset mic, or the laptop mic when testing in the browser (the browser asks for permission on the first *Wake up*). On by default; the big round mic button (page, VR, key `M`) mutes you and turns red. Echo cancellation is on; robot sound stays muted by default. Diagnostics: a green ring on the mic button shows your mic level, the status line shows `mic on N kbps` (audio actually leaving towards the robot), and the robot speaker volume is read on *Wake up* (slider next to the mic button; a warning if it is below 10 %).
   - **Voice volume** (`voicecmd.js`): anyone in the room says "Reachy, volume 5" in any language → robot speaker 50 % (0–10 → 0–100 %, "70" or "70 %" → 70 %). Found in the finished captions (original text and translation), so the caption backend must run. Number words for ~17 languages; others work through the translation or digits. The name is matched fuzzily (Whisper writes "Richi", "Ritschi", ...); a short utterance of at most 4 words like "Lautstärke 9" also counts without the name. If a volume word is heard without a command, the log says why.
   - **Speaker following is always on**, and it only reacts to confirmed speech: the mic direction counts only while the caption backend has just recognised speech (no more turning to noise), and when the speaking face is known the robot turns exactly to that face. In silence it keeps following the last person who spoke (also when they move), and it frames them like a camera operator: head centred left/right and 1/3 from the top (yaw + pitch, within the meeting head limits); when their face leaves the picture at the top or bottom edge (cut off faces are not detected), it keeps tilting that way for up to 2.5 s until the face is back. If someone speaks in front of Reachy but no face is in the picture at all (head above it: standing, or close to the robot), it tilts up step by step until a face appears. The mic array only gives a horizontal direction, up/down comes from the camera. While the followed person is in the picture, the mic direction only counts when it points clearly outside the picture (someone out of view speaks); a new direction needs 5 agreeing readings within 1 s. The VR window stays centred: the speaker yaw and the framing pitch are taken out of its pose. The backend's neural VAD sends an instant `vad` event (~0.2 s after the first word, long before any text); from then on the mic directions count, and the speaking face in view becomes the focus person.
   - Details of the DoA filter (`speaker.js`, ported from `robot/turn_to_speaker.py`): the robot slowly turns to whoever speaks (DoA from the mic array, max 80 °/s). Your head rotation is added on top of that base, so looking straight ahead = looking at the speaker. **VR view** (switch: More → World-locked / Comfort, or *Settings → VR view*; remembered per device): **world-locked** is the default (rotation-only reprojection, as in the proposal): the video window hangs where the robot camera looked when the shown frame was captured, so your own head turns are shown without lag and the robot's delay only shows as the window's edge. Frames carry no pose, so they are paired by time: the window uses the measured head pose from `videoDelayS` ago (WebRTC jitter buffer + decode from `getStats`, plus ~45 ms capture/encode; shown as `delay` in the status line). **Comfort** keeps the window and the dock still while you look into it and glides them back in front of you only after you look more than 30° away for 0.5 s (yaw only, ~0.25 s; no latency hiding). The dock follows lazily in both modes. **Room scan + panorama** (`roomscan.js`, `scene.js`): after *Wake up* Reachy looks around once (12 stops: 6 directions × 2 rows, ~20 s; *Settings → Scan the room after wake-up*, or *More → Scan room*). Each frame becomes a patch of a world-locked room panorama (the frame's video sphere at the robot pose it was taken with, soft edges, newest on top), so turning your head shows the room instantly and only moving people wait for the live video, which is drawn on top without a frame. The panorama refreshes itself whenever Reachy holds still (patch in that direction re-taken, every 2 s at most). **Depth (WP2 extension):** every patch frame also goes to the laptop (caption backend, `depth.py`, Depth Anything V2 metric indoor) and comes back as a 49×28 grid of metric depth; the patch then becomes 3D geometry (cells across a > 25 % depth jump are left out), so moving the head gives parallax. Without the backend's `[depth]` extra the panorama stays a flat sphere (status line: `room 12 depth 0(off)`). Live window and panorama share one room frame: robot world turned by the speaker-following base. **Video sphere + lens model** (`camera.js`, `scene.js`): the video is shown on a piece of sphere around the eye, a 48×27 grid over the image where every vertex sits in the direction its pixel sees; that is projection and lens undistortion in one step (and the grid the depth extension would deform). The same camera model maps image points to directions for the bubbles, face frames and face following. *Settings → Camera model*: **Auto** = our calibration (`camera.json`) when present, else the old 54° pinhole estimate (behaviour unchanged until calibrated); **Pollen factory** = the factory calibration of the Lite lens (rational + thin-prism distortion, ~82° × 61°; the daemon's scaling to 1920×1080 is unverified). JS model checked against OpenCV `projectPoints`: identical to 1e-13 px. **Safety net:** world-locked only follows the robot pose while the robot really does what is commanded (awake, fresh poses, measured pose within 30° of the command, hysteresis 15°/0.3–0.5 s); otherwise (asleep, sleep pose after a failed wake-up, wake-up motion, stuck, no pose stream) the window stands in front of you like in comfort mode. The status line shows `view world/locked` or `world/front`, and the log says why. **Vignette:** while the picture turns without your own head motion (Reachy turning to a speaker or framing a face, `turn` in the status line), the edge of the view darkens (from 6 °/s, full at 45 °/s; clear centre ~34°), the standard counter-measure against VR sickness. Grab the notes panel with the ray + pinch to put it anywhere. A new direction needs 3 agreeing speech readings within 0.6 s (±12° of their median); single outliers are ignored. Also active outside VR. There is no switch: to look elsewhere, just turn your head (or drag in the desktop preview). Point at the buttons with the controller or hand ray and pinch / pull the trigger. Pinching anywhere else recenters.
   - **Laugh** (button on the page and in VR, key `L`): Reachy laughs in three phases like a real laugh: a burst (head tilts back, both antennas spring up), a "ha-ha" rhythm (head bounces and antennas bounce in the same beat, 4.5 per second and fading, a little sway), and a settle (small nod, antennas sink briefly, then rest); ~2.7 s. Pressing again while laughing makes it stronger and longer (intensity 1.0 → 1.4). Inside the meeting limits, also while muted. `userLaughed(source, intensity)` in `app.js` is the hook for the native app, which will trigger it from the Galaxy XR face tracking (WebXR in Chrome has no face tracking).
   - **I want to talk** (also on the page): the robot turns to the center of all speakers of the last minute, the right antenna waves and the body swings ±27° for 3 s, like `robot/turn_to_speaker.py`.
   - If no camera frame arrives, the video window says *No camera image yet*. Then check the camera permission on the robot Mac.
   - **Crash diagnosis:** the log is also kept in `localStorage` (heartbeat every 5 s: heap, video, connection; plus VR end, WebGL context loss, errors). After a crash, reload and open *Debug → Previous session log*.
   - In `track` mode a watchdog bridges missing track frames with the `<video>` element after 1 s and restarts the frame reader after 3 s.
   - *Switch video* cycles how camera frames reach the VR window (`track` → `canvas` → `direct`, remembered per device). The last HUD line shows the mode and frame rates. Background in `videosource.js`: the old path uploaded the `<video>` as a plain texture whose GPU storage was fixed at the first (small, black) WebRTC frame, so VR stayed black while the page video worked.
6. Tap **Sleep** to send Reachy back to its sleep pose: motors off, camera and microphone off.

Wake up and Start are taps because browsers allow unmuted audio and entering VR only from a user gesture.

- **The headset needs no cable.** Only the Reachy Mini Lite stays on USB at the laptop, with its daemon running and signed in to HF on the dashboard. Start the daemon with `reachy-mini-daemon --no-wake-up-on-start`, so the robot sleeps until someone taps Start on the page.
- **Head limits for meetings** (`HeadMirror` in `pose.js`, same in `robot/`): pitch up 35° (someone standing or close to the robot is up to ~30° above it; 20° cut heads off), pitch down 20°, roll ±15° (hardware: ±40°). Yaw and body stay free.
- **Fast head turns are rate-limited** (`RateLimiter` in `pose.js`): head ≤ 150 °/s, body ≤ 90 °/s, braking early instead of overshooting, so the robot does not jerk or tip over.
- **How it connects:** the app uses Pollen's JS SDK `@pollen-robotics/reachy-mini-sdk@1.11.0`, loaded from jsdelivr. Pollen's central signalling finds the robot, and WebRTC carries video, `set_target` and the measured pose stream, directly between the headset and the robot.
- **Who can connect:** only HF accounts that can see the robot in central signalling.
- **OAuth:** `HF_CLIENT_ID` in `app.js` is the client ID of the HF OAuth app "Reachy Meetings XR". Its registered redirect URLs are the page URL above and `http://localhost:8080/`. Always open the page without a query string, because HF only accepts the exact registered redirect URL.

| File | Content | Uses |
|---|---|---|
| `pose.js` | Headset ↔ robot maths, recenter, `HeadMirror` (EMA, limits, body follow — same values as `robot/`) | nothing (pure, portable) |
| `robot.js` | Sign-in (silent first), auto-connect, video, `setHead()`, measured head pose | Pollen SDK |
| `scene.js` | WebXR rendering: video window at the measured robot head pose, floor grid, status panel | three.js |
| `captions.js` | Speech bubbles from `backend/` captions: always above a head in the video window (until someone is visible: top of the window in the mic direction), one per person, follows the person; mode translation+original / translation / original; thin frame around detected heads (Settings) | three.js |
| `faces.js`, `faces-worker.js` | Faces in the camera image (only faces count, never hands or backs): MediaPipe FaceLandmarker every frame (box + mouth openness), plus PoseLandmarker for heads in profile when nose and an eye are clearly visible; Web Worker on 640×360 frames, 8 Hz | DOM + media only |
| `speakers.js` | Which face is speaking (face tracks + mouth movement + DoA) and who it is: a person keeps their id ("Speaker 1", colour) by their direction in the room, also after the robot looked away; the person Reachy follows keeps their id when they walk to a new place (a lone new face within 4 s is still them) (`FaceSpeakers`) | nothing (pure, portable) |
| `roomaudio.js` | Room sound from the caption backend (binary WebSocket frames, 16 kHz PCM) via an AudioWorklet (AudioContext at 16 kHz, browser resampling) with a ~120 ms jitter buffer and a speech chain: high-pass 120 Hz against room hum, +4 dB presence at 3 kHz, compressor. Used instead of the robot's WebRTC audio, which drops ~55 % of the sound (daemon-side: 0 packets lost, measured); WebRTC audio is only the fallback when no stream arrives | DOM + media only |
| `notes.js` | Meeting notes (summary, action items, next steps from `backend/`): VR panel right of the video (drag it with the ray + pinch, or the mouse in the preview), card on the page | three.js |
| `videosource.js` | Camera frames for the VR window: WebRTC track → fixed-size 1920×1080 canvas (modes track/canvas/direct) | DOM + media only |
| `speaker.js` | Speaker following: DoA → confirmed speaker direction → slow base yaw; speakers' center | `pose.js` (pure) |
| `mic.js` | Your mic → robot speaker (replaceTrack on the SDK's audio sender), mute, re-attach after reconnects | DOM + media only |
| `voicecmd.js` | "Reachy, volume N" in any language → percent | nothing (pure, portable) |
| `camera.js` | Camera model: pixel ↔ direction with OpenCV rational + thin-prism distortion; estimate, Pollen factory, or `camera.json` | nothing (pure, portable) |
| `calib-board.html` | Checkerboard (9×6 inner corners) to show on a tablet for the calibration | – |
| `roomscan.js` | Room scan: 12 stops, when the head has settled, when to take the picture | nothing (pure, portable) |
| `gestures.js` | "I want to talk" gesture (antenna wave + body swing), layered on the mirrored pose | nothing (pure, portable) |
| `app.js` | Wires everything together, one-button UI | the four above |
| `manifest.webmanifest`, `icon*.{svg,png}` | Installable app (PWA) | |

Rules for this code: [docs/xr-client-strategy.md](../docs/xr-client-strategy.md).

### Desktop preview (debugging without a headset)

Tap **Start** in a browser without VR (any laptop): the same three.js scene opens in the browser window instead of the VR session. Drag = turn your head (the robot follows), click the VR buttons, `R` = recenter, `Esc` = back to the page.

### VR interface

- Evening sky with a warm horizon and a glowing floor (no black void); the video window has a dark rounded bezel.
- Faces: from the backend when it sends them (~20/s, backend/README.md "Faces for speaker following"), else from the headset's own detector (~3/s). The status line shows which (`people (backend) @ 19 fps`). The log marks each `speech start`, to measure how long until Reachy turns.
- Debug panel, first line: incoming video (`1920x1080 60fps 6.2Mbps VP8 …`); third line: room audio buffer and audio device delay (`stream buf 140ms out 40ms`). Fourth line: VR frame rate and the time per frame for the video upload and rendering (`frame 72 fps   js 4.1ms (video draw 2.3ms, render 1.2ms)`), to find what slows VR down. Both also go to the heartbeat log every 5 s. ⋯ → 🎞 Video switches the frame path (track / canvas / direct, `videosource.js`) for A/B tests.
- The same line warns when the robot is connected but no camera frame arrived within 10 s: the daemon's WebRTC renegotiation hung ("stuck mid-negotiation" in its log, seen after quick reconnects), reloading the page starts a fresh session.
- A red line above the dock warns while the caption server is not connected (then Reachy cannot turn to speakers: speaker following needs its speech detection).
- Dock below the view, round buttons (9 cm at 1.1 m) with the big mic button (12 cm) in the middle, so it is within ±20° of the view centre (on the far left it was ~30° out, almost outside the FOV): 🙋 Talk · 🌐 Translate (Reachy says what you say in the meeting language, see backend/README.md) · 💬 Both/Translated/Original · 🎤 · 📝 Notes · 🔇/🔊 Sound · ⋯ More → 🗣 Viktor/Siri (Reachy's voice for Translate) · ⟳ Recenter · 🐞 Debug (status panel at the top) · ✕ Exit VR. Active toggles are highlighted. (*Switch video* was removed from the UI; the modes in `videosource.js` still exist.)

### Speech bubbles

The page connects to the caption server from `backend/` (`reachy-captions`, see [backend/README.md](../backend/README.md)). It reconnects on its own, and the status line shows `captions on/off`.

- **Placement:**
  - With a speaker direction (`azimuth_deg`), the bubble floats in the room in that direction, at 0.9 × `dist`. Once the robot looks at the speaker, the bubble lands on them in the video.
  - Without a direction, the bubble is a subtitle at the bottom of the video window.
- **Content:** the translation is large, with the original small underneath. Live text (partials) is shown in italic. At most 3 bubbles are visible, and a final bubble disappears after 10 s.
- **URL:** automatic when the *Caption server* field (Settings) is empty: first `ws://localhost:8766`, then the wireless tunnel, alternating until one connects. A value in the field (stored in `localStorage`) is used instead. It is not a query parameter, because of the OAuth redirect.
- **https:** the page is served over https, so Chrome only allows `ws://localhost`. There are two ways to connect the headset:
  - **USB:** run `adb reverse tcp:8766 tcp:8766`.
  - **Wireless:** start the backend with `reachy-captions --tunnel` (see backend/README.md). Nothing to enter: the page looks up the current tunnel address on ntfy.sh and signs in with its Hugging Face login.
- **Local network permission:** recent Chrome versions (checked with Chrome 152) ask before a public page may reach `localhost` ("…wants to access devices on your local network"). Until you allow it, the captions stay at `connecting`. Allow it once per device **before** entering VR, because the prompt isn't visible inside VR. If you missed it: lock icon in the address bar → Site settings → Local network → Allow.

## World view (beta, Dominic's view mode, branch `claude/worldview`)

A separate VR view mode next to Simon's world-locked / comfort views (those stay the default and are unchanged).
**Switch on:** page *Settings → VR view → World view*, or in VR *⋯ More → 🧭 World view* (remembered per device; the
same button switches back to world-locked). In this mode *Scan room* (More, page, and "Scan the room after
wake-up") runs the world view's own look-around, *Room on/off* hides its panorama. Debug: `?lag=0.2` fixes the lag,
`?pano=4096` a bigger panorama.

What it does (files `videolag.js`, `videolag-worker.js`, `worldpolicy.js`, `worldview.js`, `worldmode.js`; hooks:
`scene.js` exposes `renderer`, `videoTexture`, `videoFrameSeq`, `setWorldView()`; `app.js` calls `worldMode` only
when the mode is on):

1. **Video lag measured from the picture** (`videolag.js`, in a Web Worker): every camera frame (straight from the
   WebRTC track, MediaStreamTrackProcessor, 128x64 grey) is phase-correlated with the previous one; the shifts are
   chained into an image rotation, converted to degrees with the camera model, and compared with the measured head
   pose over a 4 s window: lag = the time offset where the pose motion explains the image motion best (squared
   correlation, scale free), searched -150..800 ms in 5 ms steps with sub-step refinement, only windows with
   > 8 °/s RMS motion and r² > 0.6, median of the last 9 windows. Status line: `wv lag 165ms r2 0.98`.
2. **Live picture at its capture pose**: the live frame is shown at pose(t_frame − lag) instead of the current
   pose (Simon's world-locked uses pose(now − videoDelayS) with videoDelayS from `getStats`: jitter buffer + decode
   + a 45 ms guess; it misses the network and the pose path).
3. **One continuous panorama** (`worldview.js`): an equirectangular render target (2048x1024, half float, in the
   robot's world frame, turned into XR by the same room frame as Simon's panorama). Every sharp frame (the head
   moved < 1.5° around its capture time; at most 3/s; the same view again only after 1 s) is painted on the GPU at
   its capture pose with the lens model (`camera.js` maths in GLSL). Per texel the **best source wins**: quality =
   centrality in the frame (0 at its border) x stillness, the stored quality decays with age (45 s), the new frame
   replaces where it is better with a narrow soft transition: seams lie half-way between frame centres, never at a
   frame border, nothing is alpha-stacked. **Exposure**: the live frame is compared with the panorama 4x/s (32x18
   cells of ~1.4°, 16-bit readback via PBO + fence, no GPU stall), gain = median ratio (limits x1.5 per probe,
   1/3..3 overall); frames are painted with that gain, and the stored panorama is shown divided by it, so the live
   part keeps its true colours. **Display**: one sphere (r = 4 m), one shader: stored panorama (greying and dimming
   between 15 s and 150 s of age, so stale content is recognisable) with the live frame on top, faded out over its
   outer 14 % (25 % vertically): no rectangle, no frame border, no separate video window (Simon's window and bezel
   are hidden while the world view shows the robot's picture; when the robot does not follow, e.g. asleep, his
   window stands in front of you as usual).
4. **Look around** (*Scan room* in this mode): the 12 stops of `roomscan.js`, but a stop is done when a frame from
   there was **painted** (= the head really was still at the frame's capture time), timeout 6 s per stop; head
   mirroring and speaker following pause meanwhile. Afterwards the panorama keeps growing from normal use.
5. Depth / parallax: **not done**, see below.

### Measurements (2026-10-08/09)

**Lag estimator, synthetic** (`node xr-client/tests/videolag.test.mjs`; textured world, min-jerk head moves with
holds, 30 fps frames with +|N(0, 8 ms)| arrival jitter, 50 Hz poses with 4 ms jitter, noise, motion blur):
phase correlation error median 0.054°, p90 0.118° per pair; lag error over 15 runs (lags 50/120/250/400/600 ms,
3 seeds each): **mean |e| 2.8 ms, max 6.0 ms**; a still robot gives no estimate. What did not work: fitting single
frame pairs (33 ms apart): ±12..28 ms errors, because the frames' arrival jitter is ~20 % of the pair interval;
chaining the shifts and comparing motion over ~0.2 s fixed it (clean data: < 1.2 ms either way).

**Lag on the real robot, Mac side** (`robot/scripts/record_view_dataset.py`: camera via OpenCV at 60 fps next to
the daemon + `/api/state/full` polled at 140 Hz, 90 s with gentle gotos; `node xr-client/tests/lag_from_recording.mjs`):
camera vs pose lag **median +4.9 ms** (p10 −7.3, p90 +11.5, sd 7.2 ms over 64 accepted windows, r² median 0.976);
with the daemon's own pose timestamps 4.1 ms; analysing only 15 fps: 10.2 ms. The search started at 0 at first and
many windows stuck at that edge: the pose is about as late as the USB camera, so negative lags are allowed now.
Adding a known delay to the frames: +100/+300/+600 ms → **104.8/304.9/604.9 ms** (the change is recovered to
0.1 ms). In the browser (replay harness below, real frames at 7.5 fps, +150 ms added): **165 ms** (expected ~155 ms
+ up to half a frame interval of draw delay). On the headset the lag is larger (WebRTC); it is measured there
online; the status line and the log (`world view: video lag …`) show it.

**Image/pose scale 1.24 and registration on real frames** (`robot/scripts/view_registration.py`): ORB matches
between still frames from different poses (39 pairs ≥ 8° apart): with the measured poses the matched features land
**1.81° apart (median), p90 5.3°**. A constant camera mount rotation does not explain it (1.80°); scaling the
measured yaw/pitch by 1.13/1.06 does (0.91°), and a camera offset in front of the rotation centre (parallax)
explains it best (epipolar error **0.34°**): people sat 0.3-0.6 m from the robot in that recording, and near things
move more than the rotation. So the residual is parallax, not lag or pose error; only depth removes it (item 5).
In the synthetic replay (no parallax) the panorama matches the true room to **0.018° median (p90 0.035°)**
(`robot/scripts/view_pano_check.py`, phase correlation per 128 px tile).

**Pose noise at rest** (same recording): std yaw 0.23°, pitch 0.12°, peak-to-peak 0.68°; neighbouring samples
(7 ms apart) give "speeds" up to 29 °/s at rest. A speed threshold saw the head still in only 18 of ~1300 frames;
the policy uses the pose span over the capture window instead (< 1.5°; 0.8° still rejected most real frames).

**Exposure compensation** (synthetic replay, auto exposure drifting 0.7-1.3): spread of the panorama brightness
vs the true room, std over 128 px tiles: **25.8 % without → 6.9 % with** compensation. What did not work on the way:
8-bit linear probe values (dark cells quantise into useless ratios, cell ratios p25-p75 ±20 %), an 8-bit panorama
(a darker frame must be scaled up and clipped at 1.0: compensation made it worse, 14 % vs 11 %), probing only right
before a paint (new directions got a stale gain), smoothing the gain (only 60 % of each correction): fixed by 16-bit
probe values, a half-float panorama, probing 4x/s along the way, and using each probe directly.

**Cost** (Mac M1 Pro, Chrome 154; the headset is slower and not measured): worker per frame (draw + readback 128x64
+ phase correlation) 1.5-2.9 ms, window fit 1.8 ms every 0.5 s (both off the main thread); main-thread JS of the
mode per VR frame 0.01-0.05 ms without a paint; a paint is 4 passes over the frame's footprint box (~9 % of the
panorama, ~190 k texels each) + one probe pass (576 cells x 18 samples), CPU side 0.1-1.3 ms; first paint 75 ms
(shader compile, once). Per eye one full-screen sphere pass (the panorama shader with the lens model). GPU memory:
2 x 16 MB (colour, half float) + 2 x 8 MB (meta) = 48 MB at 2048x1024.

**Depth (item 5), not done**: the residual above says parallax is what is left (1.8° in a scene at 0.3-0.6 m; for
walls at 3 m with a few cm camera offset ~0.5°). It needs a depth map per painted frame from the laptop (Depth
Anything, `depth.py`), a 3D panorama instead of a sphere, and holes where depth jumps: a separate project.

### Replay harness (no robot, no headset)

`python3 -m http.server 8080 -d xr-client/pages`, then
`http://localhost:8080/dev/worldview.html?data=data/rec1&view=world&extra=0.15` (`view=simon`: the room-scan
panorama for comparison). It runs the real `scene.js` + `worldmode.js` on a virtual clock (works in a hidden or
headless tab), fed from `dev/data/rec1` (real recording, 40 s, 298 frames 480x270, 2.7 MB; more with
`robot/scripts/make_view_dataset.py export|synth`). `window.harness`: `look(yaw, pitch)`, `stats()`,
`savePanorama(name)`, `probeTest(a, b)`.
