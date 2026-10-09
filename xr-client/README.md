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
   - **Speaker following is always on**, and it only reacts to confirmed speech: the mic direction counts only while the caption backend has just recognised speech (no more turning to noise), and when the speaking face is known the robot turns exactly to that face. In silence it keeps following the last person who spoke (also when they move), and it frames them like a camera operator: head centred left/right and 1/3 from the top (yaw + pitch, within the meeting head limits); when their face leaves the picture at the top or bottom edge (cut off faces are not detected), it keeps tilting that way for up to 2.5 s until the face is back. If someone speaks in front of Reachy but no face is in the picture at all (head above it: standing, or close to the robot), it tilts up step by step until a face appears. The mic array only gives a horizontal direction, up/down comes from the camera. Who talks is decided by `follow.js` on every VAD event: a face in the picture whose mouth moves is the speaker (a second talking face takes over after 0.3 s, during an overlap or a short "mhm" only after 1.2 s); the mic direction only turns the head when nobody in the picture moves their mouth and the voice is outside the picture, and the faces take over once the person is in view. Details and measured numbers: *Speaker following* below. The VR window stays centred: the speaker yaw and the framing pitch are taken out of its pose. The backend's neural VAD sends an instant `vad` event (~0.2 s after the first word, long before any text); from then on the mic directions count, and the speaking face in view becomes the focus person.
   - Details of the DoA filter (`speaker.js`, ported from `robot/turn_to_speaker.py`): the robot turns to whoever speaks (max 100 °/s, 200 °/s²; was 80/80, a 46° turn took 1.5 s). Your head rotation is added on top of that base, so looking straight ahead = looking at the speaker. **VR view** (switch: More → World-locked / Comfort, or *Settings → VR view*; remembered per device): **world-locked** is the default (rotation-only reprojection, as in the proposal): the video window hangs where the robot camera looked when the shown frame was captured, so your own head turns are shown without lag and the robot's delay only shows as the window's edge. Frames carry no pose, so they are paired by time: the window uses the measured head pose from `videoDelayS` ago (WebRTC jitter buffer + decode from `getStats`, plus ~45 ms capture/encode; shown as `delay` in the status line). **Comfort** keeps the window and the dock still while you look into it and glides them back in front of you only after you look more than 30° away for 0.5 s (yaw only, ~0.25 s; no latency hiding). The dock follows lazily in both modes. **Room scan + panorama** (`roomscan.js`, `scene.js`): after *Wake up* Reachy looks around once (12 stops: 6 directions × 2 rows, ~20 s; *Settings → Scan the room after wake-up*, or *More → Scan room*). Each frame becomes a patch of a world-locked room panorama (the frame's video sphere at the robot pose it was taken with, soft edges, newest on top), so turning your head shows the room instantly and only moving people wait for the live video, which is drawn on top without a frame. The panorama refreshes itself whenever Reachy holds still (patch in that direction re-taken, every 2 s at most). **Depth (WP2 extension):** every patch frame also goes to the laptop (caption backend, `depth.py`, Depth Anything V2 metric indoor) and comes back as a 49×28 grid of metric depth; the patch then becomes 3D geometry (cells across a > 25 % depth jump are left out), so moving the head gives parallax. Without the backend's `[depth]` extra the panorama stays a flat sphere (status line: `room 12 depth 0(off)`). Live window and panorama share one room frame: robot world turned by the speaker-following base. **Video sphere + lens model** (`camera.js`, `scene.js`): the video is shown on a piece of sphere around the eye, a 48×27 grid over the image where every vertex sits in the direction its pixel sees; that is projection and lens undistortion in one step (and the grid the depth extension would deform). The same camera model maps image points to directions for the bubbles, face frames and face following. *Settings → Camera model*: **Auto** = our calibration (`camera.json`) when present, else the old 54° pinhole estimate (behaviour unchanged until calibrated); **Pollen factory** = the factory calibration of the Lite lens (rational + thin-prism distortion, ~82° × 61°; the daemon's scaling to 1920×1080 is unverified). JS model checked against OpenCV `projectPoints`: identical to 1e-13 px. **Safety net:** world-locked only follows the robot pose while the robot really does what is commanded (awake, fresh poses, measured pose within 30° of the command, hysteresis 15°/0.3–0.5 s); otherwise (asleep, sleep pose after a failed wake-up, wake-up motion, stuck, no pose stream) the window stands in front of you like in comfort mode. The status line shows `view world/locked` or `world/front`, and the log says why. **Vignette:** while the picture turns without your own head motion (Reachy turning to a speaker or framing a face, `turn` in the status line), the edge of the view darkens (from 6 °/s, full at 45 °/s; clear centre ~34°), the standard counter-measure against VR sickness. Grab the notes panel with the ray + pinch to put it anywhere. Also active outside VR. There is no switch: to look elsewhere, just turn your head (or drag in the desktop preview). Point at the buttons with the controller or hand ray and pinch / pull the trigger. Pinching anywhere else recenters.
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
| `speaker.js` | Smooth base yaw towards the speaker target (rate limiter), speakers' center ("I want to talk"); its own DoA filter is no longer used (Simon) | `pose.js` (pure) |
| `follow.js` | Speaker following decisions: who talks (VAD + mouth movement + mic direction), when to switch, turning towards voices out of view, framing (yaw + pitch). Time passed in, replayed by `tools/sim_follow.mjs` | nothing (pure, portable) |
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
- Dock below the view, round buttons (9 cm at 1.1 m) with the big mic button (12 cm) in the middle, so it is within ±20° of the view centre (on the far left it was ~30° out, almost outside the FOV): 🙋 Talk · 🌐 Translate (Reachy says what you say in the meeting language, see backend/README.md) · 💬 Both/Translated/Original · 🎤 · 📝 Notes · 🔇/🔊 Sound · ⋯ More → 🗣 Viktor/Siri (Reachy's voice for Translate) · 🤖 Reachy: auto/de/en/fr/it/es (the language Reachy speaks for you) · 💭 Bubbles: de/en/fr/it/es (caption language; both also in the page Settings, remembered) · ⟳ Recenter · 🐞 Debug (status panel at the top) · ✕ Exit VR. Active toggles are highlighted. (*Switch video* was removed from the UI; the modes in `videosource.js` still exist.)

### Speaker following

Goal: someone starts talking → Reachy looks at exactly that person, quickly and calmly: no turn to the wrong
person, no ping-pong, no turning back to the previous speaker.

**How it decides** (`follow.js`, called on every backend `vad` event, ~4/s while someone talks):

1. Faces in the picture get a speaking score: mouth movement (jawOpen std over 1.0 s, 0..3) + agreement with the
   mic direction (gaussian, σ 15°, 0..1). "Talks now" = jaw std > 0.03 over the last 0.5 s.
2. A talking face that is not the followed person takes over after 0.3 s. While the followed person still talks
   (overlap, "mhm"), only after 1.2 s and with a score 0.5 higher.
3. Nobody in the picture moves their mouth and the mic points outside the picture → one turn towards the voice,
   then 1.5 s for the faces to show up; faces passing by during that turn are ignored. The mic direction is the
   densest cluster (±15°, ≥ 5 readings and ≥ half of all) of the last 1 s, **only readings taken while the backend
   hears a voice** (`vad.voice`, new): in the 0.8 s the VAD stays "speaking" into a pause the mic array points
   at noise. The XVF3800 cannot tell front from back; if someone was seen at the mirrored direction and nobody
   at the direct one, Reachy turns to the mirrored one (log: `mic (behind)`).
4. The mic never fights a talking face any more: it no longer goes through `SpeakerTracker.pushDoa`, whose cluster
   of the last second reset the target after every face update. Captions no longer steer while VAD events
   arrive (the bubble picks its face once, often before the speaker is in view).

**Measured before** (headset log 2026-10-08 17:00-18:00, backend faces live at 16-20 fps;
`python xr-client/tools/analyze_follow.py --headset-only --after 17:00 -v`; the log has 1 s resolution):

| | 2026-10-08 17:00-18:00 |
|---|---|
| speech episodes | 25, 5 without any turn |
| first target change after `speech start` | median 0 s, max 3 s |
| last target change (settled) | median 4.5 s, mean 5.0 s, max 10 s |
| yaw target changes per episode | 7.3 (face 84, mic 98) |
| reversals > 10° within 2 s | 72 = 2.9 per episode, **52 of them mic vs face** |
| returns to an earlier direction | 83 |

Typical (17:55:57): `-43 by face`, `-0 by mic`, `-41 by face`, `-1 by mic` ... 16 alternations within 1 s. Before
17:00 (headset detector, ~3 fps): 3.9 reversals/min, 230 of 370 mic vs face.

**Simulator** (`node xr-client/tools/sim_follow.mjs`, 10 seeds × 90 s per scenario, closed loop through the real
page modules; world model and metrics in the file header). Scenarios: `two` people 46° apart (never both in the
picture when one is centred), `three` at -60/0/+58°, `side` with one person at 105° (behind the mic's front/back
mirror), `backchannel` (long turns with "mhm"s from the other), `chewer` (a third person moves their mouth without
talking). acquire = s until the head is within 10° of the speaker for 0.5 s (median / 90th percentile, a miss
counts as ∞), on = share of the utterance (from 1.5 s) the head is within 12° of the speaker, wrong = turns to
someone who is not talking, back = turns back to the previous speaker.

| scenario | version | acquire med / p90 | miss | on | wrong/utt | back/utt | reversals/utt | target moves/utt |
|---|---|---|---|---|---|---|---|---|
| two | before | 2.96 s / ∞ | 46 % | 46 % | 0.44 | 0.18 | 0.88 | 1.4 |
| two | after | **1.64 s / 2.72 s** | **6 %** | **78 %** | **0.00** | **0.00** | **0.08** | 1.5 |
| three | before | ∞ / ∞ | 67 % | 31 % | 2.50 | 0.45 | 7.24 | 8.2 |
| three | after | **1.94 s / ∞** | **16 %** | **58 %** | **0.00** | **0.00** | **0.04** | 1.7 |
| side | before | ∞ / ∞ | 66 % | 34 % | 3.17 | 1.41 | 8.02 | 8.8 |
| side | after | **2.12 s / ∞** | **27 %** | **49 %** | **0.01** | **0.00** | **0.04** | 1.5 |
| backchannel | before | ∞ / ∞ | 50 % | 50 % | 0.68 | 0.57 | 1.26 | 1.8 |
| backchannel | after | **1.76 s / 2.56 s** | **0 %** | **91 %** | **0.03** | **0.02** | **0.13** | 1.6 |
| chewer | before | ∞ / ∞ | 58 % | 33 % | 2.37 | 0.63 | 6.54 | 7.8 |
| chewer | after | **2.10 s / ∞** | **19 %** | **59 %** | **0.11** | **0.01** | **0.30** | 2.1 |

The simulator reproduces the logged failure: before, 7-8 target moves and 6.5-8 reversals per utterance in the
three-person scenarios (log: 7.3 moves, 2.9 reversals per episode), mostly face vs mic.

What each step brought (same seeds; on-target % two / three / side / backchannel / chewer):

| step | on-target % | notes |
|---|---|---|
| before | 46 / 31 / 34 / 50 / 33 | |
| VAD decides, mic only for voices outside the picture, no caption steering | 67 / 52 / 51 / 79 / 51 | reversals/utt 0.3-0.9 (was 0.9-8.0) |
| + voice-gated mic, overlap hold, ignore faces during a mic turn, front/back mirror | 57 / 39 / 34 / 84 / 43 | backchannel: back 0.60 → 0.03, reversals 1.44 → 0.07; wrong turns ~0 |
| + base turn 100 °/s, 200 °/s² (was 80, 80) | **78 / 58 / 49 / 91 / 59** | acquire median 2.0-2.6 s → 1.6-2.1 s |
| without the backend `voice` flag (older backend) | 66 / 57 / 50 / 88 / 55 | reversals 0.11-0.38 instead of 0.04-0.30 |
| harsher mic (±20°, 40 % reflections) | 54 / 43 / 41 / 73 / 48 | before, same noise: 45 / 34 / 34 / 49 / 35 |

Parameters that did **not** matter in the simulator (±2 points): mouth window 0.6 / 0.8 / 1.5 s, switch hold
0.25 / 0.4 s, DoA σ, DoA cluster 4 readings in 0.8 s. The remaining misses are short utterances (< 2.5 s) by
people outside the picture: the mic needs ~0.6-1 s for a reliable direction and the turn itself ~1 s.

Not done: fusing in the backend. It has VAD, DoA and faces on one clock, but the failures above were decision
logic, not timing; the page has all inputs at full rate (faces 19/s with capture time, VAD with 0.25 s events) and
the head pose at 50 Hz (the backend polls it at 2 Hz). The backend's only addition is the `voice` flag.

**Still to verify live** (all numbers above except the log are synthetic): the noise model (reflection rate, the
fan, motor noise), that `vad.voice` from Silero gates the mic readings as intended, the faster base turn in VR
(vignette, comfort), and whether the DoA is really relative to the head (`backend/doa.py` TODO). After a test:
`python xr-client/tools/analyze_follow.py --headset-only --after HH:MM -v` gives the same table as above; the
source `mic` vs `face` per move and the reversal count are the numbers to compare.

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
