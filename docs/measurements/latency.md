# Camera latency measurements

**Method** (`xr-client/pages/latency.js`, page → *Settings → Measure camera latency*): the page shows a QR code with
the current time in ms, redrawn on every screen frame. Reachy films that screen; the same page copies each received
video frame and decodes the QR. Latency = time the frame was presented in the page − time in the QR. Both ends use the
same clock, so no sync is needed.

**What it includes:** the screen showing the QR, camera exposure/readout, encoding on the robot laptop (daemon),
network, the receiver's jitter buffer and decoding. **Not included:** showing the received frame on a display
(add ~1 screen frame, ~8–17 ms, for glass to glass).

**Validity:** run it in a Chrome window that is in front and not covered by another window. Chrome throttles hidden
or covered windows; the QR then changes ~once a second and the result grows to seconds (a test with a fake camera
showed ~2000 ms for a real 150 ms). Since 2026-10-16 the page detects this, shows a warning and drops those samples.
A result is saved after 300 samples; *Copy result* gives the table row below (last column: notes).

Validation with a fake camera (known delay, browser pane at 60 fps): constant offset of ~65 ms at 0 and 150 ms delay,
spread ±3 ms (that offset is the fake camera's own video path).

| Date (UTC) | Page version | Median ms | p10–p90 ms | min/max ms | n | QR read | Page estimate ms | Video | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 2026-10-16 | 0541392 | 1840 | ? | ? | ? | ? | ? | ? | First run. **Suspect:** measured before the throttling check; the window was probably covered (chat app in front). Repeat. |
