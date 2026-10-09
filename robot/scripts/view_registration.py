"""How well do the measured head poses register the camera frames? (world view, xr-client/pages/worldview.js)

    ~/.venvs/reachy-backend/bin/python robot/scripts/view_registration.py <recording> [--lag 0.005]

For pairs of STILL frames (head moved < 0.8 deg within 0.3 s before to 0.1 s after the frame) taken at different poses, ORB features are
matched and turned into directions with the calibrated camera model (camera.json). Two numbers per pair:
  residual: angle between each matched feature's world direction from frame A and from frame B, both placed with
            the measured head pose (= the double-image error the panorama would show), median over inliers;
  rotation: the rotation that really maps A's rays onto B's (Kabsch on the inliers, RANSAC) vs the one the poses say
            (angle of the difference, and the ratio of the rotation angles = how much the pose under/over-states it).
Then fits a constant camera mount rotation (camera vs head frame) that minimises the residuals, and reports the
residuals with it.
"""

import argparse
import json
import os

import cv2
import numpy as np
from scipy.optimize import minimize
from scipy.spatial.transform import Rotation

HERE = os.path.dirname(os.path.abspath(__file__))
CAM = json.load(open(os.path.join(HERE, "..", "..", "xr-client", "pages", "camera.json")))


def rays(pts, W, H):
    """Pixel coords in a W x H image -> unit rays in the camera frame (x right, y down, z fwd)."""
    s = CAM["width"] / W
    xd, yd = (pts[:, 0] * s - CAM["cx"]) / CAM["fx"], (pts[:, 1] * s - CAM["cy"]) / CAM["fy"]
    k1, k2, p1, p2, k3 = CAM["dist"][:5]
    x, y = xd.copy(), yd.copy()
    for _ in range(40):
        r2 = x * x + y * y
        rad = 1 + k1 * r2 + k2 * r2 * r2 + k3 * r2 ** 3
        x = (xd - (2 * p1 * x * y + p2 * (r2 + 2 * x * x))) / rad
        y = (yd - (p1 * (r2 + 2 * y * y) + 2 * p2 * x * y)) / rad
    v = np.stack([x, y, np.ones_like(x)], 1)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


CAM2HEAD = np.array([[0, 0, 1], [-1, 0, 0], [0, -1, 0]], float)   # camera (right, down, fwd) -> head (fwd, left, up)


def head_rot(roll, pitch, yaw):
    return Rotation.from_euler("ZYX", [yaw, pitch, roll]).as_matrix()   # R = Rz Ry Rx, radians


def kabsch(a, b):
    """Rotation R with R a ~ b (rows = vectors)."""
    u, _, vt = np.linalg.svd(b.T @ a)
    d = np.sign(np.linalg.det(u @ vt))
    return u @ np.diag([1, 1, d]) @ vt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rec")
    ap.add_argument("--lag", type=float, default=0.005, help="frame time - lag = capture time (s)")
    ap.add_argument("--min-deg", type=float, default=8, help="only pairs at least this far apart")
    a = ap.parse_args()
    r = json.load(open(os.path.join(a.rec, "poses.json")))
    P = np.array([[p[0], p[2], p[3], p[4]] for p in r["poses"] if p[3] is not None])
    ft = np.load(os.path.join(a.rec, "frame_t.npy"))
    names = sorted(os.listdir(os.path.join(a.rec, "jpg")))
    pose_at = lambda t: np.array([np.interp(t, P[:, 0], P[:, k]) for k in (1, 2, 3)])

    def span(t0, t1):   # largest deviation from the pose at t0 (deg); speeds from neighbouring samples are quantisation noise
        m = (P[:, 0] >= t0) & (P[:, 0] <= t1)
        q = P[m]
        if len(q) < 3:
            return np.inf
        return np.degrees(np.max(np.linalg.norm(q[:, 1:] - pose_at(t0), axis=1)))

    still = []
    for nm in names:
        n = int(nm[:5])
        t = ft[n] - a.lag
        if span(t - 0.3, t + 0.1) < 0.8:
            still.append((n, t, pose_at(t)))
    # thin out: one frame per distinct pose
    keep = []
    for s in still:
        if all(np.degrees(np.linalg.norm(s[2] - k[2])) > 3 for k in keep):
            keep.append(s)
    print(f"{len(still)} still frames, {len(keep)} distinct poses")
    orb = cv2.ORB_create(3000)
    feats = {}
    for n, t, p in keep:
        g = cv2.imread(os.path.join(a.rec, "jpg", f"{n:05d}.jpg"), cv2.IMREAD_GRAYSCALE)
        g = cv2.createCLAHE(3.0, (8, 8)).apply(g)   # the room was dark
        kp, des = orb.detectAndCompute(g, None)
        feats[n] = (np.array([k.pt for k in kp]), des, g.shape)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    pairs = []
    for i in range(len(keep)):
        for j in range(i + 1, len(keep)):
            (na, ta, pa), (nb, tb, pb) = keep[i], keep[j]
            Ra, Rb = head_rot(*pa) @ CAM2HEAD, head_rot(*pb) @ CAM2HEAD   # camera -> world
            rel_pose = Rb.T @ Ra   # a-camera rays -> b-camera rays, according to the poses
            ang = np.degrees(Rotation.from_matrix(rel_pose).magnitude())
            if ang < a.min_deg or feats[na][1] is None or feats[nb][1] is None:
                continue
            m = bf.match(feats[na][1], feats[nb][1])
            if len(m) < 30:
                continue
            H, W = feats[na][2]
            ra = rays(feats[na][0][[x.queryIdx for x in m]], W, H)
            rb = rays(feats[nb][0][[x.trainIdx for x in m]], W, H)
            # RANSAC on the true relative rotation
            best, rng = None, np.random.default_rng(0)
            for _ in range(300):
                s = rng.choice(len(m), 3, replace=False)
                R = kabsch(ra[s], rb[s])
                err = np.degrees(np.arccos(np.clip(np.sum((ra @ R.T) * rb, 1), -1, 1)))
                inl = err < 0.6
                if best is None or inl.sum() > best.sum():
                    best = inl
            if best.sum() < 25:
                continue
            R_img = kabsch(ra[best], rb[best])
            res = np.degrees(np.arccos(np.clip(np.sum((ra[best] @ Ra.T) * (rb[best] @ Rb.T), 1), -1, 1)))
            diff = np.degrees(Rotation.from_matrix(R_img @ rel_pose.T).magnitude())
            ang_img = np.degrees(Rotation.from_matrix(R_img).magnitude())
            pairs.append(dict(a=na, b=nb, pose_deg=ang, img_deg=ang_img, ratio=ang_img / ang, diff=diff,
                              residual=float(np.median(res)), n=int(best.sum()), ra=ra[best], rb=rb[best], pa=pa, pb=pb))
    if not pairs:
        print("no usable pairs")
        return
    res = np.array([p["residual"] for p in pairs])
    print(f"{len(pairs)} pairs (>= {a.min_deg} deg apart, >= 25 inliers)")
    print(f"residual misregistration with the measured poses: median {np.median(res):.2f} deg, p90 {np.percentile(res, 90):.2f}, max {res.max():.2f}")
    print(f"image rotation / pose rotation: median {np.median([p['ratio'] for p in pairs]):.3f}; "
          f"rotation difference median {np.median([p['diff'] for p in pairs]):.2f} deg")
    for p in sorted(pairs, key=lambda p: p["pose_deg"])[:: max(1, len(pairs) // 12)]:
        print(f"  {p['a']:5d}-{p['b']:5d} pose {p['pose_deg']:5.1f} img {p['img_deg']:5.1f} ratio {p['ratio']:.3f} residual {p['residual']:.2f} n {p['n']}")

    def residuals(M=np.eye(3), scale=(1.0, 1.0)):
        out = []
        for p in pairs:
            sa, sb = p["pa"].copy(), p["pb"].copy()
            sa[1:] *= scale; sb[1:] *= scale
            Ra, Rb = head_rot(*sa) @ CAM2HEAD @ M, head_rot(*sb) @ CAM2HEAD @ M
            out.append(np.median(np.degrees(np.arccos(np.clip(np.sum((p["ra"] @ Ra.T) * (p["rb"] @ Rb.T), 1), -1, 1)))))
        return np.array(out)

    # 1. a constant camera mount rotation (camera vs head frame), and 2. + a scale on the measured pitch / yaw
    for name, n in [("mount rotation", 3), ("mount rotation + pitch/yaw scale", 5)]:
        f = lambda x: np.mean(residuals(Rotation.from_rotvec(x[:3]).as_matrix(), (x[3], x[4]) if n == 5 else (1, 1)))
        sol = minimize(f, np.r_[0, 0, 0, 1, 1][:n] if n == 5 else np.zeros(3), method="Nelder-Mead",
                       options={"maxiter": 2000, "xatol": 1e-5, "fatol": 1e-4, "initial_simplex": None})
        x = sol.x
        rs = residuals(Rotation.from_rotvec(x[:3]).as_matrix(), (x[3], x[4]) if n == 5 else (1, 1))
        rx, ry, rz = np.degrees(x[:3])
        extra = f", pitch scale {x[3]:.3f}, yaw scale {x[4]:.3f}" if n == 5 else ""
        print(f"fit {name}: rotation vector (camera x/y/z) {rx:.2f} {ry:.2f} {rz:.2f} deg{extra} -> residual median {np.median(rs):.2f} deg, p90 {np.percentile(rs, 90):.2f}")

    # 3. parallax: the camera sits in front of the head's rotation centre, so near things move more than the rotation.
    # With an offset o (head frame), the camera centres differ by t = (Rb - Ra) o and each match must lie on its
    # epipolar plane (angle of B's ray from the plane through A's ray and t). Only the direction of o matters.
    def epi(o):
        e = []
        for p in pairs:
            Ra, Rb = head_rot(*p["pa"]), head_rot(*p["pb"])
            wa, wb = p["ra"] @ (Ra @ CAM2HEAD).T, p["rb"] @ (Rb @ CAM2HEAD).T
            t = (Rb - Ra) @ o
            nrm = np.cross(wa, t)
            nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-12
            e.append(np.median(np.degrees(np.abs(np.arcsin(np.clip(np.sum(nrm * wb, 1), -1, 1))))))
        return np.array(e)
    best = min((minimize(lambda o: np.mean(epi(o)), np.array(d, float), method="Nelder-Mead") for d in
                ([1, 0, 0], [1, 0, 0.5], [1, 0, -0.5], [0, 0, 1], [1, 0.3, 0], [1, -0.3, 0])), key=lambda r: r.fun)
    o = best.x / np.linalg.norm(best.x)
    e = epi(best.x)
    print(f"parallax model: camera offset direction (head fwd/left/up) {np.round(o, 2)} -> epipolar error median {np.median(e):.2f} deg, "
          f"p90 {np.percentile(e, 90):.2f} (rotation-only residual median {np.median(res):.2f})")


if __name__ == "__main__":
    main()
