// World view harness: replays a dataset (robot/scripts/make_view_dataset.py) through the REAL scene.js + worldmode.js
// (or Simon's room-scan panorama, view=simon), in the desktop preview. No robot, no headset, no network.
// Frames go through a canvas that videosource.js copies (canvas mode); the lag worker gets them as small bitmaps.
// window.harness: look(yaw, pitch) turns the view, stats(), panorama() returns the panorama as RGBA bytes.

import { createScene } from "../scene.js";
import { createWorldMode } from "../worldmode.js";
import { CameraModel } from "../camera.js";
import { robotToHeadset } from "../pose.js";

const q = new URLSearchParams(location.search);
const base = q.get("data") ?? "data/rec1";
const viewKind = q.get("view") ?? "world";
const speed = Number(q.get("speed") ?? 1), extra = Number(q.get("extra") ?? 0), until = Number(q.get("until") ?? Infinity);
const fixedLag = q.get("lag") != null ? Number(q.get("lag")) : null;
const hud = document.getElementById("hud");
const log = (...a) => { console.log("[harness]", ...a); logLines.push(a.join(" ")); if (logLines.length > 8) logLines.shift(); };
const logLines = [];

const meta = await (await fetch(`${base}/meta.json`)).json();
const cam = new CameraModel(await (await fetch("../camera.json")).json());

// ---- fake camera. Deterministic replay on a virtual clock (the tab may be hidden, where browsers throttle rAF and
// timers): rAF is replaced by a MessageChannel loop that advances the data clock by `step` per frame, and the
// "video" is a canvas that videosource.js (canvas mode) copies only when the harness drew a new frame into it.
const W = 640, H = 360;
const canvas = document.createElement("canvas");
canvas.width = W; canvas.height = H;
const ctx = canvas.getContext("2d");
let fresh = false;
const video = Object.assign(canvas, { HAVE_CURRENT_DATA: 2, videoWidth: W, videoHeight: H, srcObject: null, play: async () => {} });
Object.defineProperty(video, "readyState", { get() { const r = fresh ? 4 : 1; fresh = false; return r; } });
try { localStorage.setItem("reachy-xr-video-mode", "canvas"); } catch {}
const step = Number(q.get("step") ?? 1 / 60);
let vNow = 0;   // virtual clock (s) = data time
const mc = new MessageChannel();
let rafQ = [], posted = false, hold = false;
let ticks = 0;
mc.port1.onmessage = () => {
  posted = false;
  // let other work (decoding, the worker's replies, devtools) in now and then: message tasks would starve it
  if (++ticks % 15 === 0 && globalThis.scheduler?.postTask) { holdFor(scheduler.postTask(() => {}, { priority: "user-visible" })); return; }
  const cbs = rafQ; rafQ = [];
  vNow += step;
  for (const cb of cbs) cb(vNow * 1000);
};
window.requestAnimationFrame = (cb) => {
  rafQ.push(cb);
  if (!posted && !hold) { posted = true; mc.port2.postMessage(0); }   // message tasks are not throttled in hidden tabs
  return 1;
};
window.cancelAnimationFrame = () => {};
/** Stop the clock until the promise settles (frame not decoded yet), then go on. */
function holdFor(p) {
  if (hold) return;
  hold = true;
  p.finally(() => { hold = false; if (rafQ.length && !posted) { posted = true; mc.port2.postMessage(0); } });
}

// prefetch frames a few ahead (decoding all of them would need ~0.5 GB)
const imgs = new Map();
function prefetch(i) {
  for (let k = i; k < Math.min(meta.frames.length, i + 24); k++) {
    if (imgs.has(k)) continue;
    // fetch + createImageBitmap: <img> decoding is deferred in hidden tabs
    const e = { bm: null };
    e.ready = fetch(`${base}/${meta.frames[k].f}`).then((r) => r.blob()).then((b) => createImageBitmap(b)).then((bm) => { e.bm = bm; });
    imgs.set(k, e);
  }
}

// ---- Simon's room panorama (app.js logic, ported): patch whenever the head held still (span < 1° over 0.6 s),
// every 2 s, in the nearest slot within 25° or a new one; pose = measured pose videoDelayS (0.12 s) ago.
const absHist = [];
const poseAt = (t) => {
  const h = absHist;
  if (!h.length) return null;
  for (let i = h.length - 1; i > 0; i--) if (h[i - 1][0] <= t) {
    const [t0, ...a] = h[i - 1], [t1, ...b] = h[i];
    const u = t1 > t0 ? Math.min(1, (t - t0) / (t1 - t0)) : 1;
    return a.map((v, k) => v + (b[k] - v) * u);
  }
  return h[0].slice(1);
};
const slots = new Map();
let lastSimon = 0;
function simonStep(nowS) {
  const a = poseAt(nowS - 0.12);
  if (a) scene.setRobotHead(robotToHeadset(a[0], a[1], a[2])); else scene.clearRobotHead();
  if (nowS - lastSimon < 2) return;
  const recent = absHist.filter(([t]) => nowS - t < 0.6);
  if (recent.length < 5) return;
  const span = (k) => Math.max(...recent.map((r) => r[k])) - Math.min(...recent.map((r) => r[k]));
  if (span(2) > 1 || span(3) > 1) return;
  lastSimon = nowS;
  const [, , pitch, yaw] = recent[recent.length - 1];
  let best = null, bestD = Infinity;
  for (const [key, sl] of slots) { const d = Math.hypot(((yaw - sl.yaw + 540) % 360) - 180, pitch - sl.pitch); if (d < bestD) { bestD = d; best = key; } }
  if (bestD > 25) best = `r${Math.round(yaw)}_${Math.round(pitch)}`;
  const img = scene.captureFrame(960, 540);
  if (!img || !a) return;
  scene.setRoomPatch(best, img, robotToHeadset(a[0], a[1], a[2]), null);
  slots.set(best, { pitch: a[1], yaw: a[2] });
}

// ---- the scene, like app.js builds it (room frame = identity: no speaker base, recentered straight ahead)
let worldMode = null;
const identity = { x: 0, y: 0, z: 0, w: 1 };
let t0Page = null, fi = 0, pi = 0, done = false;
const dataT0 = Math.min(meta.frames[0].t, meta.poses[0][0]);
let waiting = false;
function tick() {
  // virtual time; the frame drawn now is copied to the live texture at the start of the NEXT scene frame, so draw
  // the frames that arrive up to then (they show up on average half a frame after arrival, like on the headset)
  if (t0Page == null) t0Page = vNow;
  const nowS = vNow, tData = dataT0 + (vNow - t0Page) * speed;
  if (tData < until + dataT0) {
    while (pi < meta.poses.length && meta.poses[pi][0] <= tData) {
      const [t, r, p, y] = meta.poses[pi++];
      const tp = t0Page + (t - dataT0) / speed;
      worldMode?.pushPose(tp, r, p, y);
      absHist.push([tp, r, p, y]); if (absHist.length > 300) absHist.shift();
    }
    prefetch(fi);
    waiting = false;
    while (fi < meta.frames.length && meta.frames[fi].t + extra <= tData + step * speed) {
      const m = imgs.get(fi);
      if (!m.bm) { vNow -= step; waiting = true; holdFor(m.ready.catch(() => {})); break; }   // not loaded yet: hold the clock
      ctx.drawImage(m.bm, 0, 0, W, H); fresh = true;
      imgs.get(fi - 30)?.bm?.close(); imgs.delete(fi - 30);
      fi++; prefetch(fi);
    }
    if (fi >= meta.frames.length) done = true;
  } else done = true;
  scene.setRoomFrame(identity);
  if (waiting) return;
  if (viewKind === "simon") simonStep(nowS);
  else worldMode.frame(nowS, identity, 0.12, true, true);
}

const scene = createScene({
  video, vfovDeg: 54, cameraModel: cam, distM: 3, statusText: () => "", onHeadsetPose: () => {},
  onSelect: () => {}, onEnd: () => {}, onFrame: tick, vrButtons: [], log,
});
scene.enterDesktop();
if (viewKind !== "simon") {
  worldMode = createWorldMode({ scene, video, camera: cam, log, panoWidth: Number(q.get("pano") ?? 2048), fixedLagS: fixedLag, workerFrames: "bitmap", exposure: q.get("exposure") !== "0" });
  worldMode.setActive(true);
  // ground truth for the exposure compensation (synthetic data): which frame each probe / paint saw
  const v = worldMode.view, probe = v.probe.bind(v), paint = v.paint.bind(v);
  v.probe = (...a) => { const ok = probe(...a); if (ok) harness.probeFrames.push(fi - 1); return ok; };
  v.paint = (...a) => { const r = paint(...a); harness.paintFrames.push([fi - 1, v.stats.gain]); return r; };
} else scene.setRoomVisible(true);
prefetch(0);

// desktop "head": drag on the canvas (scene.js), done here with synthetic pointer events
function look(yawDeg, pitchDeg) {
  const c = [...document.querySelectorAll("canvas")].find((x) => x.style.position === "fixed");
  c.setPointerCapture = () => {};
  const per = 0.004 * 180 / Math.PI;   // degrees per pixel (scene.js desktop drag)
  const ev = (type, x, y, buttons) => c.dispatchEvent(new PointerEvent(type, { clientX: x, clientY: y, buttons, pointerId: 1, bubbles: true }));
  ev("pointerdown", 500, 400, 1);
  ev("pointermove", 500 + (harness.yaw - yawDeg) / per, 400 + (harness.pitch - pitchDeg) / per, 1);
  harness.yaw = yawDeg; harness.pitch = pitchDeg;
  ev("pointermove", 500 + 0.0001, 400, 0);
}

window.harness = {
  yaw: 0, pitch: 0, look, meta, probeFrames: [], paintFrames: [],
  get done() { return done; },
  stats() { return { t: fi < meta.frames.length ? meta.frames[fi].t : "end", frames: fi, mode: viewKind, wm: worldMode?.status(), lag: worldMode?.lag, view: worldMode?.view?.stats, patches: scene.roomInfo }; },
  /** Panorama colour as RGBA bytes (bottom row first), plus meta, for the analysis. */
  panorama() {
    const v = worldMode.view, rt = v.targets.color, r = scene.three.renderer;
    const n = rt.width * rt.height * 4;
    let px = new Uint8Array(n);
    const mx = new Uint8Array(n);
    if (rt.texture.type === 1016) {   // HalfFloatType: linear light -> sRGB bytes
      const h = new Uint16Array(n);
      r.readRenderTargetPixels(rt, 0, 0, rt.width, rt.height, h);
      const half = (x) => { const e = (x >> 10) & 31, m = x & 1023; return (e === 0 ? m / 1024 * 2 ** -14 : (1 + m / 1024) * 2 ** (e - 15)) * (x & 32768 ? -1 : 1); };
      const enc = (v) => (v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055);
      for (let i = 0; i < n; i++) px[i] = Math.max(0, Math.min(255, Math.round(255 * ((i & 3) === 3 ? half(h[i]) : enc(half(h[i]))))));
    } else r.readRenderTargetPixels(rt, 0, 0, rt.width, rt.height, px);
    r.readRenderTargetPixels(v.targets.meta, 0, 0, rt.width, rt.height, mx);
    return { w: rt.width, h: rt.height, px, mx };
  },
  /** Exposure probe check on two dataset frames (synthetic data: known gains). Paint A, probe B, return the gain. */
  async probeTest(iA, iB, lag = meta.lag_true ?? 0.12) {
    const THREE = await import("three");
    const v = worldMode.view;
    const P = meta.poses;
    const poseAt = (t) => { let i = P.findIndex((p) => p[0] > t); i = Math.max(1, i); const a = P[i - 1], b = P[i], u = (t - a[0]) / (b[0] - a[0]); return [1, 2, 3].map((k) => a[k] + (b[k] - a[k]) * u); };
    const tex = async (i) => {
      const bm = await createImageBitmap(await (await fetch(`${base}/${meta.frames[i].f}`)).blob());
      const c = document.createElement("canvas"); c.width = 1920; c.height = 1080; c.getContext("2d").drawImage(bm, 0, 0, 1920, 1080);
      const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.minFilter = THREE.LinearFilter; t.generateMipmaps = false; return t;
    };
    v.clear();
    const A = await tex(iA), B = await tex(iB);
    v.paint(A, poseAt(meta.frames[iA].t - lag), 1);
    v.probe(B, poseAt(meta.frames[iB].t - lag));
    for (let k = 0; k < 100 && v.probeBusy; k++) await new Promise((r) => setTimeout(r, 20));
    const s = v.stats;
    return { measured: s.gain, expected: meta.frames[iA].gain / meta.frames[iB].gain, probe: s.lastProbe };
  },
  /** POST the panorama as PNG to the dev server (?save endpoint), name.png. */
  async savePanorama(name) {
    const { w, h, px, mx } = this.panorama();
    const c = document.createElement("canvas"); c.width = w; c.height = h;
    const id = c.getContext("2d").createImageData(w, h);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const s = ((h - 1 - y) * w + x) * 4, d = (y * w + x) * 4;
      id.data[d] = px[s]; id.data[d + 1] = px[s + 1]; id.data[d + 2] = px[s + 2]; id.data[d + 3] = mx[s + 3];
    }
    c.getContext("2d").putImageData(id, 0, 0);
    const blob = await new Promise((res) => c.toBlob(res, "image/png"));
    return (await fetch(`/save?name=${encodeURIComponent(name)}`, { method: "POST", body: blob })).status;
  },
};
setInterval(() => {
  hud.textContent = `${base} view=${viewKind} speed ${speed} extra ${extra * 1000} ms ${fixedLag != null ? `lag fixed ${fixedLag}` : ""}\n` +
    `frame ${fi}/${meta.frames.length}  ${done ? "DONE" : ""}\n${worldMode?.status() ?? `patches ${scene.roomInfo.patches}`}\n${logLines.join("\n")}`;
}, 250);
