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
   - **Speaker following is always on**, and it only reacts to confirmed speech: the mic direction counts only while the caption backend has just recognised speech (no more turning to noise), and when the speaking face is known the robot turns exactly to that face. In silence it keeps following the last person who spoke (also when they move), and it frames them like a camera operator: head centred left/right and 1/3 from the top (yaw + pitch, within the meeting head limits); when their face leaves the picture at the top or bottom edge (cut off faces are not detected), it keeps tilting that way for up to 2.5 s until the face is back. If someone speaks in front of Reachy but no face is in the picture at all (head above it: standing, or close to the robot), it tilts up step by step until a face appears. The mic array only gives a horizontal direction, up/down comes from the camera. The backend's neural VAD sends an instant `vad` event (~0.2 s after the first word, long before any text); from then on the mic directions count, and the speaking face in view becomes the focus person.
   - Details of the DoA filter (`speaker.js`, ported from `robot/turn_to_speaker.py`): the robot slowly turns to whoever speaks (DoA from the mic array, max 80 °/s). Your head rotation is added on top of that base, so looking straight ahead = looking at the speaker. The VR room turns slowly around you along with it. A new direction needs 3 agreeing speech readings within 0.6 s (±12° of their median); single outliers are ignored. Also active outside VR. There is no switch: to look elsewhere, just turn your head (or drag in the desktop preview). Point at the buttons with the controller or hand ray and pinch / pull the trigger. Pinching anywhere else recenters.
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
| `notes.js` | Meeting notes (summary + action items from `backend/`): VR panel right of the video, card on the page | three.js |
| `videosource.js` | Camera frames for the VR window: WebRTC track → fixed-size canvas (modes track/canvas/direct) | DOM + media only |
| `speaker.js` | Speaker following: DoA → confirmed speaker direction → slow base yaw; speakers' center | `pose.js` (pure) |
| `mic.js` | Your mic → robot speaker (replaceTrack on the SDK's audio sender), mute, re-attach after reconnects | DOM + media only |
| `voicecmd.js` | "Reachy, volume N" in any language → percent | nothing (pure, portable) |
| `gestures.js` | "I want to talk" gesture (antenna wave + body swing), layered on the mirrored pose | nothing (pure, portable) |
| `app.js` | Wires everything together, one-button UI | the four above |
| `manifest.webmanifest`, `icon*.{svg,png}` | Installable app (PWA) | |

Rules for this code: [docs/xr-client-strategy.md](../docs/xr-client-strategy.md).

### Desktop preview (debugging without a headset)

Tap **Start** in a browser without VR (any laptop): the same three.js scene opens in the browser window instead of the VR session. Drag = turn your head (the robot follows), click the VR buttons, `R` = recenter, `Esc` = back to the page.

### VR interface

- Evening sky with a warm horizon and a glowing floor (no black void); the video window has a dark rounded bezel.
- Dock below the view: 🙋 Talk · 💬 Both/Translated/Original · 📝 Notes · 🔇/🔊 Sound · ⋯ More → ⟳ Recenter · 🐞 Debug (status panel at the top) · ✕ Exit VR. Active toggles are highlighted. (*Switch video* was removed from the UI; the modes in `videosource.js` still exist.)

### Speech bubbles

The page connects to the caption server from `backend/` (`reachy-captions`, see [backend/README.md](../backend/README.md)). It reconnects on its own, and the status line shows `captions on/off`.

- **Placement:**
  - With a speaker direction (`azimuth_deg`), the bubble floats in the room in that direction, at 0.9 × `dist`. Once the robot looks at the speaker, the bubble lands on them in the video.
  - Without a direction, the bubble is a subtitle at the bottom of the video window.
- **Content:** the translation is large, with the original small underneath. Live text (partials) is shown in italic. At most 3 bubbles are visible, and a final bubble disappears after 10 s.
- **URL:** the default is `ws://localhost:8766`. To change it, use the *Captions* field in the Debug panel; the value is stored in `localStorage`. It is not a query parameter, because of the OAuth redirect.
- **https:** the page is served over https, so Chrome only allows `ws://localhost`. There are two ways to connect the headset:
  - **USB:** run `adb reverse tcp:8766 tcp:8766`.
  - **Wireless:** expose the server via wss, e.g. `cloudflared tunnel --url http://localhost:8766`, then enter `wss://<name>.trycloudflare.com` in the field.
- **Local network permission:** recent Chrome versions (checked with Chrome 152) ask before a public page may reach `localhost` ("…wants to access devices on your local network"). Until you allow it, the captions stay at `connecting`. Allow it once per device **before** entering VR, because the prompt isn't visible inside VR. If you missed it: lock icon in the address bar → Site settings → Local network → Allow.
