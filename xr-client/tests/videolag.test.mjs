// node xr-client/tests/videolag.test.mjs
// Synthetic checks of videolag.js: phase correlation precision, and the lag estimator on a simulated robot
// (min-jerk head moves with holds, 30 fps frames with arrival jitter, ~50 Hz poses with jitter, image noise,
// motion blur) with known lag.
import { PhaseCorrelator, PoseHistory, VideoLag } from "../pages/videolag.js";

let seed = 1;
const rnd = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296);
const gauss = () => Math.sqrt(-2 * Math.log(rnd() + 1e-12)) * Math.cos(2 * Math.PI * rnd());

// world texture: smooth random blobs over 400 x 200 degrees, 4 samples per degree
const TW = 1600, TH = 800, TS = 4;
function makeWorld() {
  const t = new Float32Array(TW * TH).fill(100);
  for (let k = 0; k < 1500; k++) {
    const cx = rnd() * TW, cy = rnd() * TH, r = 3 + rnd() * 30, a = (rnd() - 0.5) * 120;
    const x0 = Math.max(0, Math.floor(cx - 3 * r)), x1 = Math.min(TW - 1, Math.ceil(cx + 3 * r));
    const y0 = Math.max(0, Math.floor(cy - 3 * r)), y1 = Math.min(TH - 1, Math.ceil(cy + 3 * r));
    for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) t[y * TW + x] += a * Math.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * r * r));
  }
  return t;
}
const world = makeWorld();
const sample = (az, el) => {   // az + = left, el + = up, degrees; world centre at (0, 0)
  const x = (200 - az) * TS, y = (100 - el) * TS;
  const xi = Math.max(0, Math.min(TW - 2, Math.floor(x))), yi = Math.max(0, Math.min(TH - 2, Math.floor(y)));
  const u = x - xi, v = y - yi, i = yi * TW + xi;
  return world[i] * (1 - u) * (1 - v) + world[i + 1] * u * (1 - v) + world[i + TW] * (1 - u) * v + world[i + TW + 1] * u * v;
};

const W = 128, H = 64, PPDX = 992 * W / 1920 * Math.PI / 180, PPDY = 993 * H / 1080 * Math.PI / 180;
function render(yaw, pitch, noise = 2, blur = null) {
  const g = new Float32Array(W * H);
  const poses = blur ?? [[yaw, pitch]];
  for (const [yw, pt] of poses) {
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      g[y * W + x] += sample(yw + (W / 2 - x) / PPDX, -pt + (H / 2 - y) / PPDY) / poses.length;
    }
  }
  for (let i = 0; i < g.length; i++) g[i] = Math.max(0, Math.min(255, g[i] + noise * gauss()));
  return g;
}

let fails = 0;
const check = (name, ok, msg) => { console.log(`${ok ? "ok  " : "FAIL"} ${name}: ${msg}`); if (!ok) fails++; };

// ---- 1. phase correlation: known sub-pixel shifts
{
  const pc = new PhaseCorrelator(W, H);
  const errs = [];
  for (let k = 0; k < 40; k++) {
    const y0 = (rnd() - 0.5) * 60, p0 = (rnd() - 0.5) * 20, dyaw = (rnd() - 0.5) * 8, dp = (rnd() - 0.5) * 6;
    pc.reset();
    pc.push(render(y0, p0));
    const r = pc.push(render(y0 + dyaw, p0 + dp));
    errs.push(Math.hypot(r.dx / PPDX - dyaw, -r.dy / PPDY - dp));
  }
  errs.sort((a, b) => a - b);
  check("phase correlation", errs[Math.floor(errs.length * 0.9)] < 0.15,
    `shift error median ${errs[errs.length >> 1].toFixed(3)}°, p90 ${errs[Math.floor(errs.length * 0.9)].toFixed(3)}° (shifts up to 4°, noise 2/255)`);
}

// ---- 2. lag estimator on a simulated robot
function minJerk(a, b, u) { u = Math.max(0, Math.min(1, u)); return a + (b - a) * (10 * u ** 3 - 15 * u ** 4 + 6 * u ** 5); }
function trajectory(T) {
  const segs = [];
  let t = 0, y = 0, p = 0;
  while (t < T + 2) {
    const ny = Math.max(-40, Math.min(40, y + (rnd() - 0.5) * 60)), np = Math.max(-15, Math.min(15, p + (rnd() - 0.5) * 20));
    const dur = 0.6 + rnd() * 1.5, hold = 0.3 + rnd() * 1.2;
    segs.push([t, t + dur, y, ny, p, np]);
    t += dur + hold; y = ny; p = np;
  }
  return (tq) => {
    for (const [t0, t1, y0, y1, p0, p1] of segs) if (tq < t1) return tq < t0 ? [y0, p0] : [minJerk(y0, y1, (tq - t0) / (t1 - t0)), minJerk(p0, p1, (tq - t0) / (t1 - t0))];
    const s = segs[segs.length - 1]; return [s[3], s[5]];
  };
}

function simulate(lagTrue, { T = 30, fps = 30, frameJitter = 0.008, poseHz = 50, poseJitter = 0.004, noise = 2 } = {}) {
  const traj = trajectory(T);
  const poses = new PoseHistory(60);
  // pose samples arrive with ~0 delay (the reference clock), with jitter in their arrival time
  for (let t = 0; t < T; t += 1 / poseHz) {
    const [y, p] = traj(t);
    poses.push(t + Math.abs(gauss()) * poseJitter, 0, p, y);
  }
  const est = new VideoLag({ pxPerDegX: PPDX, pxPerDegY: PPDY });
  const pc = new PhaseCorrelator(W, H);
  let prevT = null;
  const results = [];
  for (let tc = 1; tc < T - 1; tc += 1 / fps) {
    const blur = [-0.006, 0, 0.006].map((d) => { const [y, p] = traj(tc + d); return [y, p]; });   // ~12 ms exposure
    const g = render(0, 0, noise, blur);
    const ta = tc + lagTrue + Math.abs(gauss()) * frameJitter;   // arrival
    const r = pc.push(g);
    if (r && prevT !== null) est.addShift(prevT, ta, r.dx, r.dy, r.peak);
    prevT = ta;
    if (Math.floor(ta * 2) !== Math.floor((ta - 1 / fps) * 2)) { const w = est.update(poses, ta); if (w) results.push(w); }
  }
  return { est, results };
}

const SIM = process.env.CLEAN ? { frameJitter: 0, poseJitter: 0, noise: 0 } : {};
const SEEDS = Number(process.env.SEEDS ?? 3);
const allErr = [];
for (const lag of [0.05, 0.12, 0.25, 0.4, 0.6]) {
  const errs = [], info = [];
  for (let s = 0; s < SEEDS; s++) {
    seed = 1000 + s * 77 + Math.round(lag * 1000);
    const { est, results } = simulate(lag, SIM);
    if (process.env.VERBOSE) console.log(results.map((r) => `${(r.lag * 1000).toFixed(0)}${r.ok ? "" : "x"}`).join(" "));
    // error against the true capture time; the mean frame arrival jitter (+|N(0, 8 ms)| = +6.4 ms) is part of the
    // real lag the window placement needs, so it is not subtracted
    const err = est.lagS == null ? Infinity : est.lagS - lag;
    errs.push(err); allErr.push(Math.abs(err));
    info.push(`${results.filter((r) => r.ok).length}/${results.length} r2 ${est.quality.toFixed(2)} s ${est.scale?.toFixed(2)}`);
  }
  const mx = Math.max(...errs.map(Math.abs));
  check(`lag ${lag * 1000} ms`, mx < 0.015, `errors ${errs.map((e) => (e * 1000).toFixed(1)).join(", ")} ms (windows accepted, r2, scale: ${info.join(" | ")})`);
}
allErr.sort((a, b) => a - b);
console.log(`lag error over ${allErr.length} runs: mean |e| ${(allErr.reduce((a, b) => a + b, 0) / allErr.length * 1000).toFixed(1)} ms, max ${(allErr[allErr.length - 1] * 1000).toFixed(1)} ms`);

// ---- 3. no motion -> no estimate (must not invent a lag)
{
  const poses = new PoseHistory(60);
  for (let t = 0; t < 10; t += 0.02) poses.push(t, 0, 3, 10);
  const est = new VideoLag({ pxPerDegX: PPDX, pxPerDegY: PPDY });
  const pc = new PhaseCorrelator(W, H);
  let prevT = null;
  for (let t = 0; t < 10; t += 1 / 30) {
    const r = pc.push(render(10, 3, 4));
    if (r && prevT !== null) est.addShift(prevT, t, r.dx, r.dy, r.peak);
    prevT = t;
    est.update(poses, t);
  }
  check("still robot", est.lagS === null, `lag ${est.lagS} (expected null), last window ok=${est.last?.ok}`);
}

// ---- 4. cost: one phase correlation and one window fit
{
  const pc = new PhaseCorrelator(W, H);
  const g = render(0, 0);
  let t0 = performance.now();
  for (let i = 0; i < 200; i++) pc.push(g);
  const pcMs = (performance.now() - t0) / 200;
  const { est } = simulate(0.2, { T: 12 });
  const poses = new PoseHistory(60);
  for (let t = 0; t < 12; t += 0.02) poses.push(t, 0, 0, 30 * Math.sin(t));
  t0 = performance.now();
  for (let i = 0; i < 20; i++) est.update(poses, 11);
  console.log(`cost (node, this Mac): phase correlation ${W}x${H} ${pcMs.toFixed(2)} ms/frame, window fit ${((performance.now() - t0) / 20).toFixed(2)} ms`);
}

process.exit(fails ? 1 : 0);
