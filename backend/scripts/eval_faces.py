"""Does the face model know a person again at the sizes the robot camera sees? Measured, not guessed.

    python scripts/eval_faces.py --pairs ~/.cache/reachy-meetings/eval/lfw/pairs

Pairs of photos (LFW: 500 of the same person, 500 of two people; files <n>_0.jpg, <n>_1.jpg and labels.txt with
"<n> <1 = same person> ..." per line), each run through the same steps as the robot camera (vision.py: landmarks,
then FaceId on the face turned upright). The photos are shrunk so the eyes are as many pixels apart as on the robot:
at 1920x1080 and about 1 pixel per 0.058 degrees, eyes 6.3 cm apart are ~60 px at 1 m, ~30 px at 2 m, ~20 px at 3 m.

Per size: how many faces got an embedding at all, the equal error rate, and the similarity ranges that the
thresholds in people.py (FACE_SAME, FACE_OTHER) are read from.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from reachy_meetings_backend import vision

SIZES = (0, 60, 40, 30, 24, 18)   # eye distance in pixels; 0 = the photo as it is


def embed(finder: vision.FaceFinder, face_id: vision.FaceId, img: np.ndarray, eyes_px: float, dark: bool):
    """(embedding or None, eye distance in the picture as it was looked at)."""
    import cv2

    found = finder._detect(img, 0, 0, 1, 1)
    if not found or found[0]["pts"] is None:
        return None, 0.0
    pts = np.array(max(found, key=lambda f: f["w"])["pts"])
    native = float(np.linalg.norm(pts[1] - pts[0]))
    if eyes_px:
        k = eyes_px / native
        img = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
        if dark:   # a dim room: a quarter of the light, sensor noise on top
            noisy = img.astype(np.float32) * 0.25 + np.random.default_rng(0).normal(0, 4, img.shape)
            img = np.clip(noisy, 0, 255).astype(np.uint8)
        found = finder._detect(img, 0, 0, 1, 1)   # the landmarks as found at this size, like on the robot
        if not found or found[0]["pts"] is None:
            return None, eyes_px
        pts = np.array(max(found, key=lambda f: f["w"])["pts"])
    if not vision.FaceId.usable(pts) and eyes_px >= vision.EMB_MIN_PX:
        return None, eyes_px
    return face_id(finder._lit(img, pts), pts), float(np.linalg.norm(pts[1] - pts[0]))


def eer(same: np.ndarray, other: np.ndarray) -> tuple[float, float]:
    thr = np.sort(np.concatenate([same, other]))
    miss = np.searchsorted(np.sort(same), thr, side="left") / len(same)
    false = 1 - np.searchsorted(np.sort(other), thr, side="left") / len(other)
    i = int(np.argmin(np.abs(miss - false)))
    return float((miss[i] + false[i]) / 2), float(thr[i])


def main() -> None:
    import cv2

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pairs", default=str(Path.home() / ".cache/reachy-meetings/eval/lfw/pairs"))
    ap.add_argument("--limit", type=int, default=1000)
    args = ap.parse_args()
    root = Path(args.pairs)
    labels = [line.split()[:2] for line in (root / "labels.txt").read_text().splitlines()][: args.limit]
    finder = vision.FaceFinder(vision.FaceStream("unused")._landmarker())
    face_id = vision.FaceId()
    imgs = {(n, k): cv2.cvtColor(cv2.imread(str(root / f"{n}_{k}.jpg")), cv2.COLOR_BGR2RGB) for n, _ in labels for k in (0, 1)}
    print(f"{len(labels)} pairs\n{'eyes px':>8s} {'light':>5s} {'embedded':>9s} {'eer':>6s} {'at':>5s} | {'same p05':>8s} "
          f"{'p50':>5s} | {'other p50':>9s} {'p95':>5s} {'p99':>5s} {'max':>5s}")
    for dark in (False, True):
        for size in SIZES:
            if dark and not size:
                continue
            embs = {key: embed(finder, face_id, img, size, dark)[0] for key, img in imgs.items()}
            ok = sum(e is not None for e in embs.values())
            same, other = [], []
            for n, label in labels:
                a, b = embs[n, 0], embs[n, 1]
                if a is not None and b is not None:
                    (same if label == "1" else other).append(float(a @ b))
            if len(same) < 20 or len(other) < 20:
                print(f"{size or 'photo':>8} {'dim' if dark else 'ok':>5s} {ok / len(embs):9.0%}   too few faces found")
                continue
            same, other = np.array(same), np.array(other)
            rate, thr = eer(same, other)
            print(f"{size or 'photo':>8} {'dim' if dark else 'ok':>5s} {ok / len(embs):9.0%} {rate:6.1%} {thr:5.2f} | "
                  f"{np.percentile(same, 5):8.2f} {np.percentile(same, 50):5.2f} | {np.percentile(other, 50):9.2f} "
                  f"{np.percentile(other, 95):5.2f} {np.percentile(other, 99):5.2f} {other.max():5.2f}")


if __name__ == "__main__":
    main()
