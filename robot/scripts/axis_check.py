"""Check the head axis signs on the real robot. Watch the robot and compare with the printed expectation.

    python scripts/axis_check.py            # daemon must run (reachy-mini-daemon)

Expected convention (create_head_pose, xr-client/README.md): seen FROM BEHIND the robot,
    yaw   + = robot looks to ITS left
    pitch + = robot looks down
    roll  + = robot tilts its head to ITS right (right ear down)
"""

import json
import math
import time
import urllib.request

DAEMON = "http://127.0.0.1:8000"
STEPS = [
    ("yaw +20°   → expect: looks to ITS LEFT", 0, 0, 20),
    ("pitch +15° → expect: looks DOWN", 0, 15, 0),
    ("roll +15°  → expect: tilts head to ITS RIGHT (right ear down)", 15, 0, 0),
]


def post(path: str, body: dict | None = None) -> None:
    req = urllib.request.Request(DAEMON + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=5).read()


def goto(roll: float, pitch: float, yaw: float, duration: float = 1.5) -> None:
    pose = {"x": 0, "y": 0, "z": 0, "roll": math.radians(roll), "pitch": math.radians(pitch), "yaw": math.radians(yaw)}
    post("/api/move/goto", {"head_pose": pose, "duration": duration})
    time.sleep(duration + 0.2)


def main() -> None:
    print("Waking up…")
    post("/api/move/play/wake_up")
    time.sleep(3)
    goto(0, 0, 0)
    for text, r, p, y in STEPS:
        print(text)
        goto(r, p, y)
        time.sleep(2)
        goto(0, 0, 0)
        time.sleep(1)
    print("Done (head back to neutral).")


if __name__ == "__main__":
    main()
