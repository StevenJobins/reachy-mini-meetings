"""Record the robot camera together with the measured head pose, for the world view (xr-client/pages/worldview.js).

Reads the camera with OpenCV next to the daemon (that is fine, AVFoundation shares it) and polls the head pose from
the daemon's REST API at the same time, both stamped with time.monotonic(). Used to measure the camera-vs-pose lag
on the Mac and to build the small replay dataset for xr-client/pages/dev/worldview.html.

    ~/.venvs/reachy-backend/bin/python robot/scripts/record_view_dataset.py --out /tmp/rec --seconds 60 [--move]

--move plays gentle head motions (|yaw| <= 40 deg, pitch -15..15, slow min-jerk gotos with holds) ONLY if the
daemon's app lock is free and the motors are enabled; otherwise it records passively (e.g. while someone uses the
robot from the headset) and waits until the motors are on.

Output: poses.json ([t, t_daemon, roll, pitch, yaw, body_yaw] in s / rad), frames.npy (N x 72 x 128 uint8 grey, every
camera frame), frame_t.npy (read time of each frame), jpg/NNNNN.jpg (640x360 colour, every --jpeg-every frame).
"""

import argparse
import http.client
import json
import os
import threading
import time
from datetime import datetime

import cv2
import numpy as np

HOST, PORT = "localhost", 8000


def get(conn, path):
    conn.request("GET", path)
    r = conn.getresponse()
    return json.loads(r.read())


def post(path, body):
    c = http.client.HTTPConnection(HOST, PORT, timeout=5)
    c.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, out


def find_camera():
    from cv2_enumerate_cameras import enumerate_cameras
    for c in enumerate_cameras(cv2.CAP_AVFOUNDATION):
        if "Reachy" in c.name:
            return c.index
    raise SystemExit("Reachy Mini Camera not found")


def poll_poses(stop, out, wall_minus_mono):
    conn = http.client.HTTPConnection(HOST, PORT, timeout=2)
    while not stop.is_set():
        t0 = time.monotonic()
        try:
            s = get(conn, "/api/state/full")
        except Exception:
            conn = http.client.HTTPConnection(HOST, PORT, timeout=2)
            time.sleep(0.05)
            continue
        t1 = time.monotonic()
        hp = s.get("head_pose") or {}
        td = s.get("timestamp")
        t_daemon = (datetime.fromisoformat(td.replace("Z", "+00:00")).timestamp() - wall_minus_mono) if td else None
        out.append([(t0 + t1) / 2, t_daemon, hp.get("roll"), hp.get("pitch"), hp.get("yaw"), s.get("body_yaw"),
                    s.get("control_mode")])
        time.sleep(0.004)


def motion_script(stop):
    """Gentle head motions: slow sweeps with holds (stillness for the panorama) and some faster turns (lag)."""
    D = np.radians
    moves = []
    for yaw, pitch, dur, hold in [(0, 0, 2.0, 1.5), (35, 0, 2.5, 1.5), (35, -12, 1.5, 1.5), (0, -12, 2.0, 1.5),
                                  (-35, -12, 2.0, 1.5), (-35, 10, 1.5, 1.5), (0, 10, 2.0, 1.5), (35, 10, 2.0, 1.5),
                                  (-20, 0, 1.2, 0.8), (20, 0, 0.9, 0.8), (-20, 5, 0.9, 0.8), (20, -5, 0.9, 0.8),
                                  (-30, 0, 1.0, 0.8), (10, 8, 0.8, 0.8), (-10, -8, 0.7, 0.8), (0, 0, 1.5, 1.0)]:
        moves.append((D(yaw), D(pitch), dur, hold))
    for yaw, pitch, dur, hold in moves:
        if stop.is_set():
            return
        st, _ = post("/api/move/goto", {"head_pose": {"x": 0, "y": 0, "z": 0, "roll": 0, "pitch": float(pitch),
                                                      "yaw": float(yaw)}, "body_yaw": 0.0, "duration": dur})
        if st != 200:
            print("goto failed", st)
            return
        time.sleep(dur + hold)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--move", action="store_true")
    ap.add_argument("--jpeg-every", type=int, default=4)
    ap.add_argument("--wait-max", type=float, default=0, help="wait up to this many s for motors enabled")
    a = ap.parse_args()
    os.makedirs(os.path.join(a.out, "jpg"), exist_ok=True)

    conn = http.client.HTTPConnection(HOST, PORT, timeout=2)
    t_end_wait = time.monotonic() + a.wait_max
    while True:
        try:
            s = get(conn, "/api/state/full")
        except Exception:   # daemon restarting ("Backend not running"), connection reset
            conn = http.client.HTTPConnection(HOST, PORT, timeout=2)
            s = {}
        if s.get("control_mode") == "enabled":
            break
        if time.monotonic() > t_end_wait:
            raise SystemExit(f"motors not enabled ({s.get('control_mode')}), nothing to record")
        time.sleep(1)
    lock = get(conn, "/api/daemon/robot-app-lock-status")
    move = a.move and lock.get("state") == "free"
    print("lock", lock, "-> moving" if move else "-> passive recording")

    cap = cv2.VideoCapture(find_camera(), cv2.CAP_AVFOUNDATION)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    for _ in range(10):
        cap.read()
    stop = threading.Event()
    poses = []
    wall_minus_mono = time.time() - time.monotonic()
    th = threading.Thread(target=poll_poses, args=(stop, poses, wall_minus_mono), daemon=True)
    th.start()
    mover = threading.Thread(target=motion_script, args=(stop,), daemon=True) if move else None
    if mover:
        mover.start()
    small, ft = [], []
    t_stop = time.monotonic() + a.seconds
    n = 0
    while time.monotonic() < t_stop:
        ok, f = cap.read()
        t = time.monotonic()
        if not ok:
            continue
        small.append(cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (128, 72), interpolation=cv2.INTER_AREA))
        ft.append(t)
        if n % a.jpeg_every == 0:
            cv2.imwrite(os.path.join(a.out, "jpg", f"{n:05d}.jpg"), cv2.resize(f, (640, 360), interpolation=cv2.INTER_AREA),
                        [cv2.IMWRITE_JPEG_QUALITY, 85])
        n += 1
    stop.set()
    th.join()
    if mover:
        mover.join(timeout=0.1)
        post("/api/move/goto", {"head_pose": {"x": 0, "y": 0, "z": 0, "roll": 0, "pitch": 0, "yaw": 0}, "body_yaw": 0.0,
                                "duration": 1.5})
    np.save(os.path.join(a.out, "frames.npy"), np.array(small))
    np.save(os.path.join(a.out, "frame_t.npy"), np.array(ft))
    with open(os.path.join(a.out, "poses.json"), "w") as fh:
        json.dump({"poses": poses, "moved": move, "lock": lock, "jpeg_every": a.jpeg_every}, fh)
    dp = np.diff([p[0] for p in poses])
    print(f"{n} frames ({n / a.seconds:.1f} fps), {len(poses)} poses ({len(poses) / a.seconds:.0f} Hz, "
          f"median dt {np.median(dp) * 1000:.1f} ms)")


if __name__ == "__main__":
    main()
