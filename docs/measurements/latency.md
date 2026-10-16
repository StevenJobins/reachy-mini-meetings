# Camera latency measurements

**Method** (`xr-client/pages/latency.js`, page → *Settings → Measure camera latency*): the page shows a QR code with
the current time in ms, redrawn on every screen frame. Reachy films that screen; the same page copies each received
video frame and decodes the QR. Latency = time the frame was presented in the page − time in the QR. Both ends use the
same clock, so no sync is needed.

**What it includes:** the screen showing the QR, camera exposure/readout, encoding on the robot laptop (daemon),
network, the receiver's jitter buffer and decoding. **Not included:** showing the received frame on a display
(add ~1 screen frame, ~8–17 ms, for glass to glass).

**In the headset** (the realistic number, incl. Wi-Fi and the headset's decoder): open `qr.html` on the robot
laptop (next to Reachy, where the caption backend runs) and point Reachy at it; in the headset page choose
*Settings → Measure on this device (QR on robot laptop)*. The QR then carries the backend's clock, and both devices
sync to it over the caption WebSocket (`clocksync.js`, best of 12 pings; accuracy ± half the round trip, shown on
both screens and in the result row).

**Validity:** run it in a Chrome window that is in front and not covered by another window. Chrome throttles hidden
or covered windows; the QR then changes ~once a second and the result grows to seconds (a test with a fake camera
showed ~2000 ms for a real 150 ms). Since 2026-10-16 the page detects this, shows a warning and drops those samples.
A result is saved after 300 samples; *Copy result* gives the table row below (last column: notes).

Validation with a fake camera (known delay, browser pane at 60 fps): constant offset of ~65 ms at 0 and 150 ms delay,
spread ±3 ms (that offset is the fake camera's own video path).

| Date (UTC) | Page version | Median ms | p10–p90 ms | min/max ms | n | QR read | Page estimate ms | Video | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 2026-10-16 11:57 | e982f9e | 233 | 217–234 | 216/251 | 300 | 100 % | 136 | 1920x1080 | **Baseline.** Stock daemon on Kevin's Mac (Reachy Mini Control, no `patch_daemon_video.py`: VP8 software encoding, default bitrate), Chrome on the **same Mac** (robot daemon and measuring page on one machine, so the video does not cross Wi-Fi; with the headset add the Wi-Fi hop, and here daemon encoding and Chrome decoding share one CPU). Very steady (p10–p90 within 17 ms). Page estimate for reprojection 136 ms vs 233 ms measured. |
| 2026-10-16 13:53 | 4c51e09 | 908 | 775–1084 | 722/1148 | 300 | 88 % | 341 | 1920x1080 | **Headset** (Galaxy XR, 2D page; the row said "desktop browser" because its browser sends a desktop user agent, fixed), QR on the robot laptop (`qr.html`), clock sync ±12 ms. **Other robot laptop than the baseline:** Simon's Intel MacBook Pro (i7-8569U, 4 cores) with daemon **1.8.4** (newest that runs on Intel), caption backend on the same Mac (tiny Whisper, no face detection). Daemon at ~180 % CPU during the run, plus the Control app's camera preview (~130 %): wide spread (p10–p90 309 ms vs 17 ms) points to queueing on the overloaded laptop. **Not valid:** the "QR here" check on the same Mac right after could only redraw the QR every ~143 ms (13 of 16 GB RAM used, 7 GB swap), so this Mac distorts the measurement itself; measure with Reachy on an Apple-silicon Mac. |

Removed: two earlier rows (1840 ms, 381 ms, 2026-10-16) came from the fake-camera test page in Claude's browser pane,
not from Reachy.
