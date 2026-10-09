// node xr-client/tests/lag_from_recording.mjs <recording dir> [--clock mid|daemon] [--fps 30] [--extra 0.15]
// Runs the page's lag estimator (videolag.js, same code as the worker) over a recording of
// robot/scripts/record_view_dataset.py: grey 128x72 frames (cropped to 128x64) + head poses, both stamped on the Mac.
// --fps: analyse at most this many frames per second (the worker uses 30). --extra: add this much artificial
// delay to the frames (checks that the estimator follows a known change of the lag on real pictures).
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { PhaseCorrelator, PoseHistory, VideoLag } from "../pages/videolag.js";
import { CameraModel } from "../pages/camera.js";

const dir = process.argv[2];
const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const clock = arg("clock", "mid"), fps = Number(arg("fps", 30)), extra = Number(arg("extra", 0));

function readNpy(path) {
  const b = readFileSync(path);
  const hl = b.readUInt16LE(8), header = b.subarray(10, 10 + hl).toString();
  const shape = header.match(/'shape': \(([^)]*)\)/)[1].split(",").filter((s) => s.trim()).map(Number);
  const descr = header.match(/'descr': '([^']*)'/)[1];
  const data = b.subarray(10 + hl);
  const arr = descr === "|u1" ? new Uint8Array(data.buffer, data.byteOffset, data.length)
    : new Float64Array(data.buffer.slice(data.byteOffset, data.byteOffset + data.length));
  return { shape, arr };
}

const frames = readNpy(join(dir, "frames.npy")), ft = readNpy(join(dir, "frame_t.npy")).arr;
const rec = JSON.parse(readFileSync(join(dir, "poses.json")));
const [N, FH, FW] = frames.shape;
const cam = new CameraModel(JSON.parse(readFileSync(new URL("../pages/camera.json", import.meta.url))));
// the recorder stores 128x72 (16:9); the worker squeezes the picture to 128x64 -> crop 4 rows top and bottom here,
// so px/deg in x stays fx*128/1920 and in y fy*72/1080 (same scale as the full-height frame)
const W = 128, H = 64, y0 = (FH - H) / 2;
const pxPerDegX = cam.fx * W / cam.width * Math.PI / 180, pxPerDegY = cam.fy * FH / cam.height * Math.PI / 180;
const poses = new PoseHistory(1e9);
const D = 180 / Math.PI;
let nP = 0;
for (const p of rec.poses) {
  const t = clock === "daemon" ? p[1] : p[0];
  if (t == null || p[3] == null) continue;
  poses.push(t, p[2] * D, p[3] * D, p[4] * D); nP++;
}
const est = new VideoLag({ pxPerDegX, pxPerDegY });
const pc = new PhaseCorrelator(W, H);
let prevT = null, lastT = -1, lastUpd = -1, used = 0, peaks = [];
const wins = [];
for (let k = 0; k < N; k++) {
  const t = ft[k] + extra;
  if (t - lastT < 1 / fps - 0.004) continue;
  lastT = t;
  const g = new Float32Array(W * H);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) g[y * W + x] = frames.arr[k * FW * FH + (y + y0) * FW + x];
  const r = pc.push(g);
  if (r && prevT != null) { est.addShift(prevT, t, r.dx, r.dy, r.peak); peaks.push(r.peak); }
  prevT = t; used++;
  if (t - lastUpd > 0.5) { lastUpd = t; const w = est.update(poses, t); if (w) wins.push({ t, ...w }); }
}
const ok = wins.filter((w) => w.ok);
const fmt = (x) => (x * 1000).toFixed(1);
peaks.sort((a, b) => a - b);
console.log(`${dir}: ${N} frames (${(N / (ft[N - 1] - ft[0])).toFixed(1)} fps), ${used} analysed, ${nP} poses ` +
  `(${(nP / (poses.t[poses.length - 1] - poses.t[0])).toFixed(0)} Hz), clock ${clock}, extra ${extra * 1000} ms, median peak ${peaks[peaks.length >> 1]?.toFixed(2)}`);
console.log(`windows: ${wins.length}, accepted ${ok.length}; final lag ${est.lagS == null ? "none" : fmt(est.lagS) + " ms"}, r2 ${est.quality.toFixed(3)}, scale ${est.scale?.toFixed(3)}`);
if (ok.length) {
  const ls = ok.map((w) => w.lag).sort((a, b) => a - b);
  const q = (p) => ls[Math.min(ls.length - 1, Math.floor(p * ls.length))];
  const mean = ls.reduce((a, b) => a + b, 0) / ls.length, sd = Math.sqrt(ls.reduce((a, b) => a + (b - mean) ** 2, 0) / ls.length);
  console.log(`accepted window lags: median ${fmt(q(0.5))} ms, p10 ${fmt(q(0.1))}, p90 ${fmt(q(0.9))}, mean ${fmt(mean)} sd ${fmt(sd)} ms; ` +
    `r2 median ${ok.map((w) => w.r2).sort()[ok.length >> 1].toFixed(3)}, scale median ${ok.map((w) => w.scale).sort()[ok.length >> 1].toFixed(3)}`);
}
if (process.argv.includes("--windows")) for (const w of wins) console.log(`  t ${w.t.toFixed(1)} lag ${fmt(w.lag)} r2 ${w.r2.toFixed(3)} s ${w.scale.toFixed(2)} motion ${w.motion.toFixed(1)} n ${w.n} ${w.ok ? "ok" : ""}`);
