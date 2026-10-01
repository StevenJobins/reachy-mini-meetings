# robot

Reachy Mini control: head-pose mirroring, expressive gestures (head / antennas), audio output for translated speech.

## `turn_to_speaker.py` – turn towards whoever is speaking

Uses the Direction of Arrival (DoA) of the Reachy Mini's ReSpeaker mic array (daemon endpoint `/api/state/doa`) to turn head and body towards the current speaker in one smooth motion.

- **Speaker tracking:** every speech reading updates the target; head and body turn together (max. 200°/s). Front/back cannot be distinguished by the mic array – a speaker behind the robot is reached by turning continuously.
- **"I want to speak" button:** press Enter in the terminal → Reachy turns to the centre of all speaker directions of the last 60 s (each 20° sector counts once, so everyone feels addressed), waves the right antenna and swings its body ±27° for 3 s.

```bash
# Reachy Mini Control App must be running (daemon on localhost:8000)
pip install reachy-mini numpy
python robot/turn_to_speaker.py   # Enter = I want to speak, Ctrl+C = stop
```
