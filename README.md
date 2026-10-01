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
| `docs/` | Proposal, architecture, meeting notes, report |

## Team

| Name | Focus |
|---|---|
| Dominic | |
| | |
| | |

## Decisions

- **XR client: web app first, native app later.** Rules and roadmap: [docs/xr-client-strategy.md](docs/xr-client-strategy.md)

## Getting started

- Teleop in the headset: open https://stevenjobins.github.io/reachy-mini-meetings/. Details in [xr-client/README.md](xr-client/README.md).
- Robot side: [robot/README.md](robot/README.md)
