// Video latency relative to the pose stream, measured from the picture itself. Pure logic: no DOM, no three.js
// (runs in node for the tests and in videolag-worker.js).
//
// Frames carry no pose, so the world view needs to know how old the picture is compared to the measured head pose.
// Idea: while the robot turns, the whole picture shifts. Phase correlation of consecutive small grey frames gives that
// shift (px -> degrees with the camera model); the measured head pose gives the same motion, only earlier. The lag
// is the time offset L at which the pose motion explains the image motion best:
//   observed shift of frame pair (t1, t2)  ~  s * (pose(t2 - L) - pose(t1 - L))
// fitted over a sliding window (scale s free, so the score is a squared correlation), L in 0..maxLag.
// Time base: whatever clock the caller uses for poses and frames (the page: performance.now() / 1000 at arrival).

// ---------------------------------------------------------------- FFT + phase correlation

/** In-place radix-2 complex FFT of length n (power of 2). inverse: unscaled inverse transform. */
export function fft(re, im, inverse = false) {
  const n = re.length;
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      let t = re[i]; re[i] = re[j]; re[j] = t;
      t = im[i]; im[i] = im[j]; im[j] = t;
    }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const ang = (inverse ? 2 : -2) * Math.PI / len;
    const wr = Math.cos(ang), wi = Math.sin(ang);
    for (let i = 0; i < n; i += len) {
      let cr = 1, ci = 0;
      for (let k = 0; k < len / 2; k++) {
        const a = i + k, b = a + len / 2;
        const xr = re[b] * cr - im[b] * ci, xi = re[b] * ci + im[b] * cr;
        re[b] = re[a] - xr; im[b] = im[a] - xi;
        re[a] += xr; im[a] += xi;
        const t = cr * wr - ci * wi; ci = cr * wi + ci * wr; cr = t;
      }
    }
  }
}

/** 2-D FFT (rows then columns) of a w x h complex image stored row-major. */
export function fft2(re, im, w, h, inverse = false) {
  const rr = new Float64Array(w), ri = new Float64Array(w);
  for (let y = 0; y < h; y++) {
    const o = y * w;
    for (let x = 0; x < w; x++) { rr[x] = re[o + x]; ri[x] = im[o + x]; }
    fft(rr, ri, inverse);
    for (let x = 0; x < w; x++) { re[o + x] = rr[x]; im[o + x] = ri[x]; }
  }
  const cr = new Float64Array(h), ci = new Float64Array(h);
  for (let x = 0; x < w; x++) {
    for (let y = 0; y < h; y++) { cr[y] = re[y * w + x]; ci[y] = im[y * w + x]; }
    fft(cr, ci, inverse);
    for (let y = 0; y < h; y++) { re[y * w + x] = cr[y]; im[y * w + x] = ci[y]; }
  }
}

/**
 * Global shift between consecutive grey frames (w x h, both powers of 2). push(grey) returns
 * {dx, dy, peak} = how far the content of this frame moved against the previous one (px, + = right / down), and the
 * height of the correlation peak (0..1; ~1 = clean pure shift, < ~0.05 = no reliable match), or null for the first.
 */
export class PhaseCorrelator {
  constructor(w, h, { lowpass = 0.35 } = {}) {
    Object.assign(this, { w, h });
    this.win = new Float64Array(w * h);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      this.win[y * w + x] = (0.5 - 0.5 * Math.cos(2 * Math.PI * (x + 0.5) / w)) * (0.5 - 0.5 * Math.cos(2 * Math.PI * (y + 0.5) / h));
    }
    // Gaussian weight on the cross-power spectrum: video compression noise lives in the high frequencies.
    this.lp = new Float64Array(w * h);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const fx = (x <= w / 2 ? x : x - w) / w, fy = (y <= h / 2 ? y : y - h) / h;
      this.lp[y * w + x] = lowpass ? Math.exp(-(fx * fx + fy * fy) / (2 * lowpass * lowpass * 0.25)) : 1;
    }
    this.prevRe = null; this.prevIm = null;
  }

  reset() { this.prevRe = this.prevIm = null; }

  push(grey) {
    const { w, h } = this, n = w * h;
    const re = new Float64Array(n), im = new Float64Array(n);
    let mean = 0;
    for (let i = 0; i < n; i++) mean += grey[i];
    mean /= n;
    for (let i = 0; i < n; i++) re[i] = (grey[i] - mean) * this.win[i];
    fft2(re, im, w, h);
    const pr = this.prevRe, pi = this.prevIm;
    this.prevRe = re; this.prevIm = im;
    if (!pr) return null;
    // cross-power spectrum F_cur * conj(F_prev), normalised -> peak at the shift of cur relative to prev
    const cr = new Float64Array(n), ci = new Float64Array(n);
    let wsum = 0;
    for (let i = 0; i < n; i++) {
      const a = re[i] * pr[i] + im[i] * pi[i], b = im[i] * pr[i] - re[i] * pi[i];
      const m = Math.hypot(a, b);
      if (m < 1e-9) continue;
      cr[i] = a / m * this.lp[i]; ci[i] = b / m * this.lp[i];
      wsum += this.lp[i];
    }
    fft2(cr, ci, w, h, true);
    let best = -Infinity, bi = 0;
    for (let i = 0; i < n; i++) if (cr[i] > best) { best = cr[i]; bi = i; }
    const bx = bi % w, by = (bi - bx) / w;
    const at = (x, y) => cr[((y + h) % h) * w + ((x + w) % w)];
    const sub = (m, c, p) => { const d = m - 2 * c + p; return d < 0 ? Math.max(-0.5, Math.min(0.5, 0.5 * (m - p) / d)) : 0; };
    let dx = bx + sub(at(bx - 1, by), best, at(bx + 1, by));
    let dy = by + sub(at(bx, by - 1), best, at(bx, by + 1));
    if (dx > w / 2) dx -= w;
    if (dy > h / 2) dy -= h;
    return { dx, dy, peak: best / (wsum || 1) };
  }
}

// ---------------------------------------------------------------- pose history

/** Measured head pose over time: [t, roll, pitch, yaw] (s, degrees; pitch + = down, yaw + = left), ring buffer. */
export class PoseHistory {
  constructor(keepS = 12) { this.keepS = keepS; this.t = []; this.p = []; }

  push(t, roll, pitch, yaw) {
    if (this.t.length && t <= this.t[this.t.length - 1]) return;
    this.t.push(t); this.p.push([roll, pitch, yaw]);
    let k = 0;
    while (k < this.t.length && this.t[k] < t - this.keepS) k++;
    if (k > 64) { this.t.splice(0, k); this.p.splice(0, k); }
  }

  get length() { return this.t.length; }
  get last() { return this.t.length ? this.t[this.t.length - 1] : -Infinity; }

  /** Index of the last sample at or before t (-1 if none). */
  idx(t) {
    let lo = 0, hi = this.t.length - 1;
    if (hi < 0 || t < this.t[0]) return -1;
    while (lo < hi) { const m = (lo + hi + 1) >> 1; if (this.t[m] <= t) lo = m; else hi = m - 1; }
    return lo;
  }

  /** [roll, pitch, yaw] at time t, linearly interpolated (yaw unwrapped), clamped to the ends; null if empty. */
  at(t, out = [0, 0, 0]) {
    const n = this.t.length;
    if (!n) return null;
    const i = this.idx(t);
    if (i < 0) { out[0] = this.p[0][0]; out[1] = this.p[0][1]; out[2] = this.p[0][2]; return out; }
    if (i >= n - 1) { const q = this.p[n - 1]; out[0] = q[0]; out[1] = q[1]; out[2] = q[2]; return out; }
    const a = this.p[i], b = this.p[i + 1], u = (t - this.t[i]) / (this.t[i + 1] - this.t[i]);
    let dyaw = b[2] - a[2];
    if (dyaw > 180) dyaw -= 360; else if (dyaw < -180) dyaw += 360;
    out[0] = a[0] + (b[0] - a[0]) * u; out[1] = a[1] + (b[1] - a[1]) * u; out[2] = a[2] + dyaw * u;
    return out;
  }

  /** Largest angular speed (deg/s, yaw scaled by cos(pitch)) between t0 and t1, from the samples in between. */
  maxSpeed(t0, t1) {
    const n = this.t.length;
    if (n < 2) return Infinity;
    let i = Math.max(0, this.idx(t0)), v = 0;
    const end = Math.min(n - 1, Math.max(i + 1, this.idx(t1) + 1));
    for (; i < end; i++) {
      const dt = this.t[i + 1] - this.t[i];
      if (dt <= 0) continue;
      const a = this.p[i], b = this.p[i + 1];
      let dy = b[2] - a[2];
      if (dy > 180) dy -= 360; else if (dy < -180) dy += 360;
      const c = Math.cos(a[1] * Math.PI / 180);
      v = Math.max(v, Math.hypot(dy * c, b[1] - a[1], b[0] - a[0]) / dt);
    }
    return v;
  }
}

// ---------------------------------------------------------------- lag estimator

/**
 * Online lag estimator. addShift() per frame pair (times t1 < t2 in the pose clock, shift in px of the small frame),
 * update(poses, now) every ~0.5 s. lagS = smoothed estimate (null until the first good window), quality = squared
 * correlation of the last accepted window (0..1), scale = image motion / pose motion there (~1 if the camera model and
 * signs are right).
 */
export class VideoLag {
  constructor({
    pxPerDegX, pxPerDegY,          // small-frame pixels per degree near the image centre (camera model)
    maxLagS = 0.8, stepS = 0.005, windowS = 4, baselineS = 0.2,
    minMotionDegS = 8,             // RMS pose speed in the window, below: not enough motion to tell
    minR2 = 0.6, minPeak = 0.04,
    keep = 9,                      // accepted window estimates kept for the median
  }) {
    Object.assign(this, { pxPerDegX, pxPerDegY, maxLagS, stepS, windowS, baselineS, minMotionDegS, minR2, minPeak, keep });
    this.frames = [];              // [t, accumulated content rotation x, y (deg), chain id]
    this.chain = 0;
    this.accepted = [];            // [t, lag, r2]
    this.lagS = null; this.quality = 0; this.scale = null; this.last = null;
  }

  /** Content of the frame at t2 moved by (dx, dy) small-frame px against the frame at t1 (phase correlation). */
  addShift(t1, t2, dx, dy, peak) {
    // Frames are chained into an accumulated image angle; a bad match (or a gap) starts a new chain. The fit compares
    // motion over ~baselineS instead of single frame pairs: the arrival jitter of single frames (~5-10 ms on 33 ms)
    // would otherwise dominate (synthetic test: +-20 ms lag error with single pairs, see the README).
    const f = this.frames, last = f[f.length - 1];
    if (!(t2 > t1) || peak < this.minPeak) { this.chain++; return; }
    if (!last || last[3] !== this.chain || Math.abs(last[0] - t1) > 1e-6) {
      if (last && last[3] === this.chain) this.chain++;
      f.push([t1, 0, 0, this.chain]);
    }
    const p = f[f.length - 1];
    // content moves right when the camera turns left (yaw +), and up when it looks down (pitch +)
    f.push([t2, p[1] + dx / this.pxPerDegX, p[2] - dy / this.pxPerDegY, this.chain]);
    while (f.length && f[0][0] < t2 - this.windowS - 2) f.shift();
  }

  /** Pairs [t1, t2, ox, oy] about baselineS apart within a chain, ending in the last windowS seconds. */
  pairs(now) {
    const f = this.frames, out = [];
    let j = 0;
    for (let k = 0; k < f.length; k++) {
      if (f[k][0] <= now - this.windowS) continue;
      while (j < k && (f[j][3] !== f[k][3] || f[k][0] - f[j][0] > this.baselineS)) j++;
      if (j < k && f[j][3] === f[k][3] && f[k][0] - f[j][0] > this.baselineS / 2) out.push([f[j][0], f[k][0], f[k][1] - f[j][1], f[k][2] - f[j][2]]);
    }
    return out;
  }

  /** Fit the lag over the last windowS seconds. Returns the window result {lag, r2, scale, motion, ok} or null. */
  update(poses, now) {
    const P = this.pairs(now);
    if (P.length < 10 || poses.length < 10) return null;
    const a = [0, 0, 0], b = [0, 0, 0];
    // pose motion in the window (at L = 0; the lag only shifts it)
    let mm = 0, mt = 0;
    for (const [t1, t2] of P) {
      poses.at(t1, a); poses.at(t2, b);
      const c = Math.cos(a[1] * Math.PI / 180);
      mm += ((b[2] - a[2]) * c) ** 2 + (b[1] - a[1]) ** 2;
      mt += (t2 - t1) ** 2;
    }
    const motion = Math.sqrt(mm / Math.max(mt, 1e-9));
    const scores = [];
    let best = -1, bestL = 0, bestS = 0;
    for (let L = 0; L <= this.maxLagS + 1e-9; L += this.stepS) {
      let op = 0, pp = 0, oo = 0;
      for (const [t1, t2, ox, oy] of P) {
        poses.at(t1 - L, a); poses.at(t2 - L, b);
        let dyaw = b[2] - a[2];
        if (dyaw > 180) dyaw -= 360; else if (dyaw < -180) dyaw += 360;
        const px = dyaw * Math.cos(a[1] * Math.PI / 180), py = b[1] - a[1];
        op += ox * px + oy * py; pp += px * px + py * py; oo += ox * ox + oy * oy;
      }
      const r2 = op > 0 && pp > 0 && oo > 0 ? (op * op) / (pp * oo) : 0;
      scores.push(r2);
      if (r2 > best) { best = r2; bestL = L; bestS = pp > 0 ? op / pp : 0; }
    }
    // sub-step refinement (parabola through the best score and its neighbours)
    const k = Math.round(bestL / this.stepS);
    if (k > 0 && k < scores.length - 1) {
      const m = scores[k - 1], c = scores[k], p = scores[k + 1], d = m - 2 * c + p;
      if (d < 0) bestL += this.stepS * Math.max(-0.5, Math.min(0.5, 0.5 * (m - p) / d));
    }
    // the optimum must be a clear peak, not an edge of the search range
    const edge = k === 0 || k === scores.length - 1;
    const ok = motion >= this.minMotionDegS && best >= this.minR2 && !edge && bestS > 0.5 && bestS < 2;
    const res = { lag: bestL, r2: best, scale: bestS, motion, n: P.length, ok };
    this.last = res;
    if (ok) {
      this.accepted.push([now, bestL, best]);
      if (this.accepted.length > this.keep) this.accepted.shift();
      const ls = this.accepted.map((x) => x[1]).sort((x, y) => x - y);
      this.lagS = ls[ls.length >> 1];
      this.quality = best; this.scale = bestS;
    }
    return res;
  }
}
