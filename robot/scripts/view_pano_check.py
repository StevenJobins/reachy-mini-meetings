import sys, cv2, numpy as np
p = cv2.imread(sys.argv[1], cv2.IMREAD_UNCHANGED).astype(np.float32)
t = cv2.imread(sys.argv[2]).astype(np.float32)
if t.shape[:2] != p.shape[:2]:
    t = cv2.resize(t, (p.shape[1], p.shape[0]), interpolation=cv2.INTER_AREA)
cov = p[:, :, 3] > 250
lin = lambda x: (x / 255) ** 2.2
pl, tl = lin(p[:, :, :3]).mean(2), lin(t).mean(2)
m = cov & (tl > 0.02) & (pl > 0.02)
r = pl[m] / tl[m]
print(f"coverage {cov.mean()*100:.1f}%  brightness ratio pano/truth: median {np.median(r):.3f} p10 {np.percentile(r,10):.3f} p90 {np.percentile(r,90):.3f}")
# per 128x128 tile: brightness ratio (exposure consistency) and residual shift (registration, phase correlation)
H, W = pl.shape
deg_per_px = 360 / W
shifts, ratios = [], []
for y in range(0, H - 127, 64):
    for x in range(0, W - 127, 64):
        c = cov[y:y+128, x:x+128]
        if c.mean() < 0.98:
            continue
        a, b = pl[y:y+128, x:x+128], tl[y:y+128, x:x+128]
        if b.std() < 0.02:
            continue
        win = cv2.createHanningWindow((128, 128), cv2.CV_32F)
        (dx, dy), resp = cv2.phaseCorrelate(b.astype(np.float32), a.astype(np.float32), win)
        if resp > 0.2:
            shifts.append(np.hypot(dx, dy) * deg_per_px)
        ratios.append(np.median(a[b > 0.02] / b[b > 0.02]))
s = np.array(shifts); q = np.array(ratios)
print(f"tiles {len(s)}: residual shift median {np.median(s):.3f} deg, p90 {np.percentile(s,90):.3f}, max {s.max():.3f}")
print(f"tile brightness ratio: median {np.median(q):.3f}, spread p10-p90 {np.percentile(q,10):.3f}-{np.percentile(q,90):.3f} (std/median {q.std()/np.median(q)*100:.1f}%)")
