# Head motion measurements

Measured on 2026-10-30 with a Reachy Mini Lite and daemon 1.8.0 on Kevin's Mac, for placing the video in the headset (`xr-client/unity`, `ViewLogic.cs`).

## Does the image turn as much as the head says?

**Method:** head moved to known angles with `/api/move/goto`, 2.2 s to settle, one photo per pose from the robot camera, in a large hall (far objects, so the camera's sideways shift does not matter). SIFT matches between two photos, unprojected with our calibration, give the rotation of the camera between them (Kabsch + RANSAC, 280–950 inliers, residual 0.05–0.24°).

| Pair | Commanded | Measured pose | Image |
|---|---|---|---|
| yaw −25 → +25 | 50° | 43.0° | 44.2° |
| yaw −15 → +15 | 30° | 22.1° | 23.0° |
| pitch −10 → +10 | 20° | 16.7° | 17.4° |
| yaw 0 again, coming from +25 / −25 | 0° | 10.8° apart | 11.7° apart |

**Results:**

- **Field of view:** the residual is smallest at the calibrated 88° (scale 1.00–1.03); Pollen's ~82° fits clearly worse.
- **Camera tilt:** none. The yaw axis is (−0.01, 1.00, 0.00) and the pitch axis (1.00, 0.02, −0.06) in the camera frame.
- **Measured pose:** tracks the image to about 3 %, so it can be trusted. The pose stream is too uneven to place the window directly, not wrong.
- **Commands:** the head does not reach them. The gap depends on where it came from.

## Mechanical play

**Method:** 72 Hz targets over `/api/move/ws/set_target`, 60 Hz measured pose by daemon timestamp, on the laptop itself (no Wi-Fi).

| Command | Measured span |
|---|---|
| ±30° yaw, 4 s period | 53.7° of 60° |
| ±6° yaw, 2 s period | 3.3° of 12° |
| ±3° yaw, 1.5 s period | 0.2° of 6° |
| ±15° yaw steps | 29.6° of 30° |

Best fit: play of ±3° in yaw and ±2° in pitch, delay 120 ms. Roll was not measured (1.5° assumed).

Not known: whether the cause is friction, weak position control or the kinematics. Compensating for it in the commands could make small head movements reach the robot, but might make it twitch; not tried.
