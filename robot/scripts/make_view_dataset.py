"""Replay datasets for the world view harness (xr-client/pages/dev/worldview.html).

    python robot/scripts/make_view_dataset.py synth  <out dir>               # synthetic room, known lag + exposure
    python robot/scripts/make_view_dataset.py export <recording> <out dir> [--start s] [--seconds s] [--every n]

Format: <out>/meta.json {"frames": [{"f": "00012.jpg", "t": s}], "poses": [[t, roll, pitch, yaw] deg], ...},
<out>/*.jpg. Times in seconds, frames stamped when they arrived, poses when they arrived (the page's view of it).

synth: a procedural room (equirectangular, walls with patterns and text), seen through the calibrated camera model
(xr-client/pages/camera.json: same distortion maths as camera.js) along a robot-like head trajectory (min-jerk moves
with holds), frames 15 fps arriving `--lag` s after their capture, with an auto-exposure gain that drifts per frame
(tests the exposure compensation). Ground truth for the panorama: the painted panorama must match the room.
"""

import argparse
import json
import os
import shutil

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
CAMERA = os.path.join(HERE, "..", "..", "xr-client", "pages", "camera.json")


def camera():
    c = json.load(open(CAMERA))
    return c


def make_room(w=4096, h=2048, seed=3):
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w, 3), np.uint8)
    # walls: vertical gradient, floor darker, ceiling lighter
    for y in range(h):
        lat = 90 - 180 * (y + 0.5) / h
        base = 170 if lat > 30 else (120 if lat > -25 else 70)
        img[y, :] = (base * 0.9, base, base * 1.05)
    for _ in range(260):   # posters, windows, furniture
        cx, cy = rng.integers(0, w), rng.integers(int(h * 0.25), int(h * 0.75))
        bw, bh = rng.integers(40, 260), rng.integers(30, 200)
        col = tuple(int(v) for v in rng.integers(20, 240, 3))
        cv2.rectangle(img, (int(cx), int(cy)), (int(cx + bw), int(cy + bh)), col, -1)
        cv2.rectangle(img, (int(cx), int(cy)), (int(cx + bw), int(cy + bh)), (20, 20, 20), 3)
    for k in range(0, w, 128):   # grid lines + labels: misregistration is easy to see
        cv2.line(img, (k, int(h * 0.2)), (k, int(h * 0.8)), (240, 240, 240), 2)
        cv2.putText(img, f"{180 - k * 360 // w}", (k + 6, int(h * 0.5)), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (10, 10, 10), 3)
    for y in range(int(h * 0.2), int(h * 0.8), 96):
        cv2.line(img, (0, y), (w, y), (230, 230, 230), 1)
    return img


def rot(roll, pitch, yaw):
    r, p, y = np.radians([roll, pitch, yaw])
    Rz = np.array([[np.cos(y), -np.sin(y), 0], [np.sin(y), np.cos(y), 0], [0, 0, 1]])
    Ry = np.array([[np.cos(p), 0, np.sin(p)], [0, 1, 0], [-np.sin(p), 0, np.cos(p)]])
    Rx = np.array([[1, 0, 0], [0, np.cos(r), -np.sin(r)], [0, np.sin(r), np.cos(r)]])
    return Rz @ Ry @ Rx


_rays = {}


def camera_rays(c, W, H):
    """Unit-less rays (x/z, y/z, 1) of the W x H output pixels, through the lens model (cached)."""
    if (W, H) not in _rays:
        u, v = np.meshgrid((np.arange(W) + 0.5) * c["width"] / W, (np.arange(H) + 0.5) * c["height"] / H)
        xd, yd = (u.ravel() - c["cx"]) / c["fx"], (v.ravel() - c["cy"]) / c["fy"]
        k1, k2, p1, p2, k3 = c["dist"][:5]
        x, y = xd.copy(), yd.copy()
        for _ in range(60):   # fixed point on the OpenCV model (small distortion: converges to 1e-12)
            r2 = x * x + y * y
            rad = 1 + k1 * r2 + k2 * r2 * r2 + k3 * r2 ** 3
            x = (xd - (2 * p1 * x * y + p2 * (r2 + 2 * x * x))) / rad
            y = (yd - (p1 * (r2 + 2 * y * y) + 2 * p2 * x * y)) / rad
        und = np.stack([x, y], 1)
        _rays[(W, H)] = np.concatenate([und, np.ones((len(und), 1))], 1)
    return _rays[(W, H)]


def render(room, c, pose, W=640, H=360):
    d_cam = camera_rays(c, W, H)
    # camera (x right, y down, z fwd) -> head (x fwd, y left, z up)
    d_head = np.stack([d_cam[:, 2], -d_cam[:, 0], -d_cam[:, 1]], 1)
    d = d_head @ rot(*pose).T
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    lon, lat = np.arctan2(d[:, 1], d[:, 0]), np.arcsin(np.clip(d[:, 2], -1, 1))
    h, w = room.shape[:2]
    mx = ((0.5 - lon / (2 * np.pi)) * w - 0.5).astype(np.float32).reshape(H, W)
    my = ((0.5 - lat / np.pi) * h - 0.5).astype(np.float32).reshape(H, W)
    return cv2.remap(room, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)


def min_jerk(a, b, u):
    u = min(1.0, max(0.0, u))
    return a + (b - a) * (10 * u ** 3 - 15 * u ** 4 + 6 * u ** 5)


def synth(out, lag=0.12, fps=15, seconds=40, seed=5):
    rng = np.random.default_rng(seed)
    os.makedirs(out, exist_ok=True)
    room = make_room()
    cv2.imwrite(os.path.join(out, "truth.jpg"), cv2.resize(room, (2048, 1024), interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 80])
    c = camera()
    segs, t, y, p = [], 0.5, 0.0, 0.0
    while t < seconds:   # look around the whole room, with holds
        ny, np_ = float(np.clip(y + rng.uniform(25, 70) * rng.choice([-1, 1]), -170, 170)), float(rng.uniform(-15, 15))
        dur, hold = rng.uniform(0.7, 1.6), rng.uniform(0.6, 1.8)
        segs.append((t, t + dur, y, ny, p, np_))
        t, y, p = t + dur + hold, ny, np_

    def pose_at(tq):
        for t0, t1, y0, y1, p0, p1 in segs:
            if tq < t1:
                return (0.0, p0, y0) if tq < t0 else (0.0, min_jerk(p0, p1, (tq - t0) / (t1 - t0)), min_jerk(y0, y1, (tq - t0) / (t1 - t0)))
        s = segs[-1]
        return (0.0, s[5], s[3])

    poses = [[round(tq, 4), *[round(v, 4) for v in pose_at(tq)]] for tq in np.arange(0, seconds, 0.02)]
    frames, gain = [], 1.0
    for k, tc in enumerate(np.arange(0.2, seconds - 0.2, 1 / fps)):
        img = render(room, c, pose_at(tc)).astype(np.float32)
        # auto exposure: smooth drift between 0.7 and 1.3 (a real camera adapts over ~0.5-2 s, not per frame)
        gain = float(1 + 0.2 * np.sin(2 * np.pi * tc / 17) + 0.1 * np.sin(2 * np.pi * tc / 6.3 + 1))
        img = np.clip(img * gain + rng.normal(0, 2, img.shape), 0, 255).astype(np.uint8)
        f = f"{k:05d}.jpg"
        cv2.imwrite(os.path.join(out, f), img, [cv2.IMWRITE_JPEG_QUALITY, 82])
        frames.append({"f": f, "t": round(float(tc + lag), 4), "gain": round(gain, 3)})
    json.dump({"frames": frames, "poses": poses, "note": f"synthetic room, lag {lag} s, {fps} fps, exposure drift",
               "lag_true": lag, "truth": "truth.jpg"}, open(os.path.join(out, "meta.json"), "w"))
    print(f"{len(frames)} frames, {len(poses)} poses -> {out}")


def export(rec, out, start=0.0, seconds=40.0, every=2, width=480):
    r = json.load(open(os.path.join(rec, "poses.json")))
    ft = np.load(os.path.join(rec, "frame_t.npy"))
    jpgs = sorted(os.listdir(os.path.join(rec, "jpg")))
    t0 = ft[0] + start
    os.makedirs(out, exist_ok=True)
    frames = []
    for i, name in enumerate(jpgs):
        if i % every:
            continue
        n = int(name[:5])
        t = float(ft[n])
        if t < t0 or t > t0 + seconds:
            continue
        img = cv2.imread(os.path.join(rec, "jpg", name))
        img = cv2.resize(img, (width, width * 9 // 16), interpolation=cv2.INTER_AREA)
        cv2.imwrite(os.path.join(out, name), img, [cv2.IMWRITE_JPEG_QUALITY, 78])
        frames.append({"f": name, "t": round(t - t0, 4)})
    D = 180 / np.pi
    poses = [[round(p[0] - t0, 4), round(p[2] * D, 3), round(p[3] * D, 3), round(p[4] * D, 3)]
             for p in r["poses"] if p[3] is not None and t0 - 2 <= p[0] <= t0 + seconds + 1]
    json.dump({"frames": frames, "poses": poses, "note": f"real recording {os.path.basename(rec)} from {start} s"},
              open(os.path.join(out, "meta.json"), "w"))
    size = sum(os.path.getsize(os.path.join(out, f)) for f in os.listdir(out))
    print(f"{len(frames)} frames, {len(poses)} poses, {size / 1e6:.1f} MB -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["synth", "export"])
    ap.add_argument("args", nargs="+")
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--seconds", type=float, default=40)
    ap.add_argument("--every", type=int, default=2)
    ap.add_argument("--lag", type=float, default=0.12)
    a = ap.parse_args()
    if a.cmd == "synth":
        synth(a.args[0], lag=a.lag)
    else:
        if os.path.exists(a.args[1]):
            shutil.rmtree(a.args[1])
        export(a.args[0], a.args[1], a.start, a.seconds, a.every)
