"""Calibrate the Reachy Mini Lite camera at the resolution the headset gets (1920x1080), with an AprilTag grid,
from the real camera (calibrate_camera.py is the checkerboard tool for frames captured on the web page).
--camera-json writes the result in the format the page loads (xr-client/pages/camera.json, camera.js).

The VR window needs the camera's real field of view (xr-client `vfov`, until now an estimate: 54 deg) and the
lens distortion; Pollen's factory values are for 3840x2592 with an unverified crop factor for 1080p.

Target: an AprilTag grid (Kalibr "aprilgrid"): --cols x --rows tags of --tag cm with --gap cm between them,
tag ids row by row. Show it to Reachy's camera and move it around: near and far, all corners and edges of the
picture, tilted. A view is kept when enough tags are found and it differs from the views kept so far; after
--frames views it calibrates and writes calibration_1080p.json.

    python robot/scripts/calibrate_camera_apriltag.py --camera 0 --out ~/Desktop/reachy-calib
    python robot/scripts/calibrate_camera_apriltag.py --from-images --out ~/Desktop/reachy-calib \
        --camera-json xr-client/pages/camera.json

Runs next to the daemon (macOS lets two apps read the camera). Needs opencv-python (with aruco) and numpy.
"""

import argparse
import json
import math
import time
from pathlib import Path

import cv2
import numpy as np

FAMILIES = {"36h11": cv2.aruco.DICT_APRILTAG_36h11, "25h9": cv2.aruco.DICT_APRILTAG_25h9,
            "16h5": cv2.aruco.DICT_APRILTAG_16h5, "36h10": cv2.aruco.DICT_APRILTAG_36h10}
# Pollen's factory calibration for the Lite (reachy_mini 1.11 camera_constants.py), at 3840x2592,
# and their crop factor for the 1920x1080 mode (marked "TODO check" there)
FACTORY_FX, FACTORY_FY, FACTORY_SIZE, FACTORY_CROP = 2001.81, 2003.08, (3840, 2592), 1.115


def fov(f: float, size: int) -> float:
    return math.degrees(2 * math.atan(size / 2 / f))


SQUARE = [(0, 0), (1, 0), (1, 1), (0, 1)]
# OpenCV's corner order per tag did NOT match the printed grid (the first run, assuming it did, gave 133 px
# error). All 8 rotations / mirrorings are tried and the one with the smallest homography error is used.
CORNER_ORDERS = [(o if not rev else o[::-1]) for rev in (False, True)
                 for o in (SQUARE[k:] + SQUARE[:k] for k in range(4))]


def board_corners(ids, cols, tag, pitch, order):
    """3D corners (z = 0) of the given tag ids; ids row by row (which way up does not matter: a mirrored
    board is the same board turned over, still a valid planar target)."""
    pts = []
    for i in ids:
        r, c = divmod(int(i), cols)
        pts += [(c * pitch + dx * tag, r * pitch + dy * tag, 0) for dx, dy in order]
    return np.array(pts, np.float32)


def homography_error(views, cols, tag, pitch, order):
    errs = []
    for ids, img in views:
        obj = board_corners(ids, cols, tag, pitch, order)[:, :2]
        H, _ = cv2.findHomography(obj, img, 0)
        errs.append(np.median(np.linalg.norm(cv2.perspectiveTransform(obj.reshape(-1, 1, 2), H).reshape(-1, 2) - img, axis=1)))
    return float(np.median(errs))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", type=int, default=0, help="AVFoundation index of 'Reachy Mini Camera'")
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--rows", type=int, default=6)
    ap.add_argument("--tag", type=float, default=8.3, help="tag size, cm")
    ap.add_argument("--gap", type=float, default=2.49, help="space between tags, cm")
    ap.add_argument("--family", choices=[*FAMILIES, "auto"], default="auto")
    ap.add_argument("--min-tags", type=int, default=8)
    ap.add_argument("--frames", type=int, default=25)
    ap.add_argument("--out", type=Path, default=Path("."))
    ap.add_argument("--from-images", action="store_true", help="recalibrate from view_*.jpg in --out")
    ap.add_argument("--camera-json", type=Path, help="also write the model for the page (camera.js format)")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    pitch = args.tag + args.gap
    n_tags = args.cols * args.rows

    saved = sorted(args.out.glob("view_*.jpg")) if args.from_images else []
    cap = None if saved else cv2.VideoCapture(args.camera, cv2.CAP_AVFOUNDATION)
    if cap:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detectors = {name: cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(d), params)
                 for name, d in FAMILIES.items() if args.family in ("auto", name)}
    family = None
    views, kept = [], []   # views: (ids, image corners); kept: (centre x, centre y, width) of each kept view
    size, last_print = None, 0.0
    print(f"show the {args.cols}x{args.rows} AprilTag grid to the camera, collecting {args.frames} views", flush=True)
    while saved if args.from_images else len(views) < args.frames:
        if saved:
            frame = cv2.imread(str(saved.pop(0)))
            kept.clear()   # take every saved view
        else:
            ok, frame = cap.read()
            if not ok:
                continue
        size = frame.shape[1], frame.shape[0]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        best = (None, None, None)
        for name, det in (detectors.items() if family is None else [(family, detectors[family])]):
            corners, ids, _ = det.detectMarkers(gray)
            if ids is not None:
                ids = ids.ravel()
                good = ids < n_tags
                if best[1] is None or good.sum() > len(best[1]):
                    best = (name, ids[good], [c for c, g in zip(corners, good) if g])
        name, ids, corners = best
        now = time.time()
        if ids is None or len(ids) < (4 if args.from_images else args.min_tags):
            if args.from_images:
                continue
            if now - last_print > 5:
                print(f"  {0 if ids is None else len(ids)} tags in view (need {args.min_tags}), "
                      f"brightness {gray.mean():.0f}", flush=True)
                last_print = now
            continue
        img = np.concatenate([c.reshape(-1, 2) for c in corners]).astype(np.float32)
        if family is None:
            family = name
            print(f"  family tag{family}", flush=True)
        cx, cy = img.mean(0) / size
        span = np.ptp(img[:, 0]) / size[0]
        if any(math.hypot(cx - kx, cy - ky) < 0.08 and abs(span - ks) < 0.06 for kx, ky, ks in kept):
            continue   # too similar to a view we already have
        kept.append((cx, cy, span))
        views.append((ids, img))
        if not args.from_images:
            cv2.imwrite(str(args.out / f"view_{len(views):02d}.jpg"), frame)
        print(f"  view {len(views)}/{args.frames}: {len(ids)} tags, centre ({cx:.2f}, {cy:.2f}), "
              f"width {span:.2f} of the image", flush=True)
        last_print = now
    if cap:
        cap.release()

    order = min(CORNER_ORDERS, key=lambda o: homography_error(views, args.cols, args.tag, pitch, o))
    img = [c for _, c in views]

    def calibrate(gap_ratio, flags=0):
        obj = [board_corners(ids, args.cols, args.tag, args.tag * (1 + gap_ratio), order) for ids, _ in views]
        return cv2.calibrateCamera(obj, img, size, None, None, flags=flags)

    # Gap/tag ratio: with the stated 2.49/8.3 = 0.30 every view was off by ~10 px; the error has a clear
    # minimum elsewhere (0.57 on 2026-10-08, fx = fy there). The field of view hardly depends on it
    # (56.8-57.4 deg for 0.53-0.60), so fit it instead of trusting the tape measure.
    ratios = np.arange(0.2, 0.91, 0.01)
    errs = [calibrate(r)[0] for r in ratios]
    ratio = float(ratios[int(np.argmin(errs))])
    print(f"  gap/tag ratio: stated {args.gap / args.tag:.2f} -> rms {calibrate(args.gap / args.tag)[0]:.2f} px, "
          f"best fit {ratio:.2f} -> rms {min(errs):.2f} px", flush=True)
    rms5, K5, D5, _, _ = calibrate(ratio)
    rms, K, D, _, _ = calibrate(ratio, cv2.CALIB_RATIONAL_MODEL)   # richer model, to compare
    rms, K, D, rms5, K5, D5 = rms5, K5, D5, rms, K, D   # report the 5-coefficient model, the rational one as comparison
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    s = size[0] / FACTORY_SIZE[0] * FACTORY_CROP   # what Pollen's factory values predict for this resolution
    res = {
        "size": size, "views": len(views), "family": f"tag{family}", "gap_tag_ratio_fit": ratio, "rms_px": rms,
        "fx": fx, "fy": fy, "cx": cx, "cy": cy, "dist": D.ravel().tolist(),
        "vfov_deg": fov(fy, size[1]), "hfov_deg": fov(fx, size[0]),
        "principal_offset_px": [cx - size[0] / 2, cy - size[1] / 2],
        "model_rational": {"rms_px": rms5, "fx": K5[0, 0], "fy": K5[1, 1], "dist": D5.ravel().tolist(),
                           "vfov_deg": fov(K5[1, 1], size[1])},
        "factory_prediction": {"fx": FACTORY_FX * s, "fy": FACTORY_FY * s,
                               "vfov_deg": fov(FACTORY_FY * s, size[1]), "hfov_deg": fov(FACTORY_FX * s, size[0])},
    }
    (args.out / "calibration_1080p.json").write_text(json.dumps(res, indent=2))
    if args.camera_json:
        # The 5-coefficient model, padded to camera.js's 12 (k1 k2 p1 p2 k3, k4-k6 and s1-s4 zero): the rational
        # model fitted no better here (same rms), so the extra terms would only extrapolate at the edges.
        d = D.ravel().tolist()[:5] + [0.0] * 7
        args.camera_json.write_text(json.dumps({
            "name": f"calibrated {time.strftime('%Y-%m-%d')} (AprilTag grid, real camera)",
            "width": size[0], "height": size[1], "fx": fx, "fy": fy, "cx": cx, "cy": cy,
            "dist": d, "rms": round(float(rms), 3), "model": "standard (5 coefficients)", "frames": len(views),
            "board": [args.cols, args.rows],
        }, indent=2) + "\n")
        print(f"wrote {args.camera_json}", flush=True)
    print(json.dumps(res, indent=2), flush=True)


if __name__ == "__main__":
    main()
