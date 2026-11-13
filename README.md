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

**For a meeting**, two things run: the laptop next to Reachy, and the headset of the person who is somewhere else.

1. **On the laptop (a Mac with Apple Silicon):** open the *Reachy Meetings* app. It starts everything the meeting
   needs and shows whether each part is up, the pairing code, and the headsets that may connect
   ([laptop-app/README.md](laptop-app/README.md)). Reachy itself is driven by Pollen's own app, *Reachy Mini
   Control*, which must be installed too; *Reachy Meetings* opens it when it is not running.
2. **In the headset:** open *Reachy Meetings*. A headset that is new to this laptop shows a number pad: type the
   six-digit code from the laptop's window, once, while both are on the same Wi-Fi. No cable, no address.
3. **From another network (at home):** switch on *Other networks* in the laptop's window. A paired headset then
   finds the laptop by itself, wherever it is.

Waking Reachy, sending it to sleep and restarting it after an error are in the headset's menu (*Reachy* tile).

| To | Do |
|---|---|
| build the laptop app | `laptop-app/build_app.sh --standalone` makes `laptop-app/dist/standalone/Reachy Meetings.app` (120 MB, for any Apple Silicon Mac; it fetches the speech and vision libraries itself on its first start) |
| publish an update for the laptop apps | `laptop-app/release.sh <version>`, then the `gh release create` line it prints; the apps update themselves |
| run it from a terminal instead | `./start_meeting.sh`, with `--remote` for other networks (needs this checkout and the backend's environment) |
| build and install the headset app | [xr-client/unity/README.md](xr-client/unity/README.md) |

How the headset reaches the laptop (one door with a token per headset, pairing, other networks) is described in
[xr-client/unity/README.md](xr-client/unity/README.md), *Robot video*; the door itself is `robot/scripts/gateway.py`.

- The earlier web app (a reference only, see *Decisions*): https://stevenjobins.github.io/reachy-mini-meetings/, details in [xr-client/README.md](xr-client/README.md).
- Robot side: [robot/README.md](robot/README.md)
