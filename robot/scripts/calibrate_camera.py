"""Calibrate the robot camera from checkerboard frames captured on the web page -> xr-client/pages/camera.json.

1. Wake Reachy up on the page (desktop browser), open Settings -> "Checkerboard" on a tablet / second screen.
2. Hold the board in front of Reachy at many angles and distances, also near the edges and corners of the
   picture (that is where the lens distorts most), and press "Capture calibration frame" (or auto-capture).
   30-40 frames (the board must be fully visible). The frames are exactly the stream the headset sees.
3. python scripts/calibrate_camera.py ~/Downloads/reachy-calib-*.png
4. Commit xr-client/pages/camera.json: the page loads it and uses it ("Camera model: Auto").

Model: OpenCV pinhole with the rational + thin-prism distortion (12 coefficients), the same as Pollen's factory
calibration of the Lite lens, which seeds the solver. Needs: pip install opencv-python numpy (4.x or 5.x)

Why 30+ frames and full coverage (simulated with this lens, synthetic board views, 0.15 px corner noise): the
corner fit is always ~0.2 px, but the error over the WHOLE picture was 6-21 px with 16 frames and ~2.6 px
(max 4 px at 960x540) with 30-40 well spread frames. Fewer distortion terms (rational 8, standard 5) left
~13 px: this lens needs the full model, and the full model needs the data. The script warns about both.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO / "xr-client" / "pages" / "camera.json"

# Pollen's factory calibration (reachy_mini 1.11 camera_constants.ReachyMiniLiteCamSpecs, full sensor 3840x2592)
FACTORY_K = np.array([[2001.8076426486707, 0.0, 1905.876059826701],
                      [0.0, 2003.0778885944105, 1328.3239717935594],
                      [0.0, 0.0, 1.0]])
FACTORY_D = np.array([-1.4652320301298614, 0.6542714131667414, 0.012147809271745049, -0.002677286460143648,
                      0.3035939941825349, -1.4300809080461876, 0.570024082887235, 0.3567299243352951,
                      0.003057363348400015, 0.0003357614008682464, -0.009897126394310923, -0.002050919484589521])


def factory_guess(w: int, h: int, crop: float = 1.115) -> tuple[np.ndarray, np.ndarray]:
    """Factory calibration scaled to the stream like the daemon does (camera_utils.intrinsics_for_size)."""
    K = FACTORY_K.copy()
    K[0, 0] *= w / 3840 * crop
    K[1, 1] *= h / 2592 * crop
    K[0, 2] = FACTORY_K[0, 2] / 3840 * w
    K[1, 2] = FACTORY_K[1, 2] / 2592 * h
    return K, FACTORY_D.copy()


def find_corners(path: Path, pattern: tuple[int, int]) -> tuple[np.ndarray | None, tuple[int, int]]:
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None, (0, 0)
    size = (img.shape[1], img.shape[0])
    ok, corners = cv2.findChessboardCornersSB(img, pattern, flags=cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY)
    if not ok:
        ok, corners = cv2.findChessboardCorners(img, pattern, cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE)
        if ok:
            corners = cv2.cornerSubPix(img, corners, (11, 11), (-1, -1),
                                       (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 1e-3))
    return (corners if ok else None), size


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+", type=Path, help="PNG/JPG frames from the page (reachy-calib-*.png)")
    ap.add_argument("--cols", type=int, default=9, help="inner corners per row (calib-board.html: 9)")
    ap.add_argument("--rows", type=int, default=6, help="inner corners per column (calib-board.html: 6)")
    ap.add_argument("--model", choices=["rational", "standard"], default="rational",
                    help="rational: 12 coefficients like Pollen's (default); standard: k1 k2 p1 p2 k3 (fewer frames)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    pattern = (args.cols, args.rows)
    board = np.zeros((args.cols * args.rows, 3), np.float32)
    board[:, :2] = np.mgrid[0:args.cols, 0:args.rows].T.reshape(-1, 2)   # square size 1: scale does not matter

    obj, img, size, used = [], [], None, []
    for p in sorted(args.images):
        corners, s = find_corners(p, pattern)
        if size and s != size:
            print(f"skip {p.name}: size {s} != {size}")
            continue
        if corners is None:
            print(f"no board: {p.name}")
            continue
        size = s
        obj.append(board)
        img.append(corners.reshape(-1, 1, 2).astype(np.float32))
        used.append(p.name)
    print(f"board found in {len(used)} of {len(args.images)} frames")
    if len(used) < 8:
        print("need at least 8 (better 20+) frames with the whole board visible")
        return 1

    w, h = size
    # Coverage: which parts of the picture saw board corners? Uncovered regions (often the corners, where the
    # lens distorts most) are extrapolated by the 12-term model and can be badly off.
    gx, gy = 6, 4
    seen = np.zeros((gy, gx), bool)
    for c in img:
        pts = c.reshape(-1, 2)
        seen[np.clip((pts[:, 1] / h * gy).astype(int), 0, gy - 1), np.clip((pts[:, 0] / w * gx).astype(int), 0, gx - 1)] = True
    names = {(0, 0): "top left", (0, gx - 1): "top right", (gy - 1, 0): "bottom left", (gy - 1, gx - 1): "bottom right"}
    missing = [names.get((j, i), f"cell row {j + 1} col {i + 1}") for j in range(gy) for i in range(gx) if not seen[j, i]]
    print(f"picture covered by board corners: {seen.mean() * 100:.0f} % of a {gx}x{gy} grid")
    if missing:
        print("  WARNING: never covered: " + ", ".join(missing) + " -> capture more frames with the board there")
    if len(used) < 25:
        print(f"  WARNING: only {len(used)} usable frames; with this lens model aim for 30+ (see the docstring)")

    K, D = factory_guess(w, h)
    if args.model == "rational":
        flags = cv2.CALIB_USE_INTRINSIC_GUESS | cv2.CALIB_RATIONAL_MODEL | cv2.CALIB_THIN_PRISM_MODEL
    else:
        flags, D = cv2.CALIB_USE_INTRINSIC_GUESS, np.zeros(5)
    rms, K, D, _, _ = cv2.calibrateCamera(obj, img, size, K, D, flags=flags,
                                          criteria=(cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 200, 1e-9))
    D = np.asarray(D).ravel()[:12].tolist()
    D += [0.0] * (12 - len(D))

    def fov(axis_len: int, f: float, c: float) -> float:   # rough (undistorted) field of view, for the printout
        return float(np.degrees(np.arctan(c / f) + np.arctan((axis_len - c) / f)))

    out = {
        "name": f"calibrated {dt.date.today().isoformat()}",
        "width": w, "height": h,
        "fx": float(K[0, 0]), "fy": float(K[1, 1]), "cx": float(K[0, 2]), "cy": float(K[1, 2]),
        "dist": D, "rms": round(float(rms), 3),
        "model": args.model, "frames": len(used), "board": [args.cols, args.rows],
    }
    args.out.write_text(json.dumps(out, indent=2) + "\n")
    print(f"rms reprojection error {rms:.3f} px  (good: < 0.5, ok: < 1)")
    print(f"fx {out['fx']:.1f} fy {out['fy']:.1f} cx {out['cx']:.1f} cy {out['cy']:.1f}  "
          f"~{fov(w, out['fx'], out['cx']):.0f}° x {fov(h, out['fy'], out['cy']):.0f}° (pinhole part)")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
