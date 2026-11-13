# Reachy Mini Meetings

ETH Zurich – Mixed Reality course project.
Supervisors: Zuria Bauer, Rene Zurbrugg

A Reachy Mini robot acts as the physical avatar of a remote meeting participant. The remote person joins through an XR headset (Meta Quest / Galaxy XR), sees the meeting room through the robot, and is represented in the room by the robot's head and antenna movements.

## Scope

- **Latency-hiding 3D reprojection** – reproject the robot's camera stream to the current head pose so motion-to-photon latency is hidden in the headset
- **Translated speech bubbles** – live transcription + translation, shown as speech bubbles above each speaker in the headset view
- **Two-way translation** – the remote user's speech is translated and spoken by the robot in the room
- **Expressive feedback** – head and antenna gestures (nodding, attention, reactions) driven by the remote user
- **Meeting copilot panel** – Granola-style live notes, summary and action items in the headset UI

Out of scope: spatial audio.

## Repository layout

| Folder | Content |
|---|---|
| `xr-client/` | Headset app (Quest / Galaxy XR): video, reprojection, speech bubbles, copilot panel |
| `robot/` | Reachy Mini control: head-pose mirroring, gestures, audio out |
| `backend/` | Streaming, speech-to-text, translation, TTS, copilot |
| `laptop-app/` | The Mac app for the laptop next to Reachy: starts and watches gateway and backend, pairing code, headsets |
| `docs/` | Proposal, architecture, meeting notes, report |

## Team

| Name | Focus |
|---|---|
| Dominic | |
| | |
| | |

## Decisions

- **XR client: the Unity app (`xr-client/unity`) is the way forward** (decided 2026-10-30); the web app is only a reference. Background: [docs/xr-client-strategy.md](docs/xr-client-strategy.md)

## Getting started

**For a meeting:** on the laptop next to Reachy open the *Reachy Meetings* app ([laptop-app/README.md](laptop-app/README.md); built with `laptop-app/build_app.sh`, with `--standalone` for a Mac without this project), or run `./start_meeting.sh` (starts Reachy Mini Control, the caption backend and the gateway through which the headset reaches both, and shows whether each part is up), then open *Reachy Meetings* in the headset. A headset that is new to this laptop asks once for the six-digit code the laptop shows. With `./start_meeting.sh --remote` a paired headset can also join from another network (at home). Waking Reachy, sending it to sleep and restarting it after an error are in the headset's menu (*Reachy* tile).

- Teleop in the headset: open https://stevenjobins.github.io/reachy-mini-meetings/. Details in [xr-client/README.md](xr-client/README.md).
- Robot side: [robot/README.md](robot/README.md)
