// Web Worker for videolag.js: camera frames -> small grey frames -> phase correlation -> lag fit. Nothing of this
// runs on the page's main thread (VR frame budget).
//
// Messages in:
//   {stream: ReadableStream<VideoFrame>}           frames straight from the WebRTC track (MediaStreamTrackProcessor)
//   {frame: VideoFrame | ImageBitmap, t}          one frame (fallback when the stream cannot be transferred), t = epoch ms
//   {poses: [[t, roll, pitch, yaw], ...]}          measured head poses (t = epoch ms of arrival)
//   {camera: {pxPerDegX, pxPerDegY}}               small-frame pixels per degree (camera model)
// Messages out (every ~0.5 s): {lag, quality, scale, window: {...}, fps, ms}  (lag in s, null until known)
// Clock: epoch milliseconds (performance.timeOrigin + performance.now()), the same in the page and here.

import { PhaseCorrelator, PoseHistory, VideoLag } from "./videolag.js";

const W = 128, H = 64, MAX_FPS = 30;
const canvas = new OffscreenCanvas(W, H);
const ctx = canvas.getContext("2d", { willReadFrequently: true });
const pc = new PhaseCorrelator(W, H);
const poses = new PoseHistory(15);
let lag = null;
let prevT = null, lastT = 0, nFrames = 0, msSum = 0, lastReport = 0;
const now = () => performance.timeOrigin + performance.now();

function setCamera({ pxPerDegX, pxPerDegY }) {
  lag = new VideoLag({ pxPerDegX, pxPerDegY });
}

/** One frame (VideoFrame or ImageBitmap) that arrived at tMs. Closes it. */
function onFrame(frame, tMs) {
  try {
    if (!lag || tMs - lastT < 1000 / MAX_FPS - 4) return;   // ~30 fps is plenty, and halves the work at 60 fps
    lastT = tMs;
    const t0 = performance.now();
    ctx.drawImage(frame, 0, 0, W, H);
    const px = ctx.getImageData(0, 0, W, H).data;
    const g = new Float32Array(W * H);
    for (let i = 0, j = 0; i < g.length; i++, j += 4) g[i] = 0.299 * px[j] + 0.587 * px[j + 1] + 0.114 * px[j + 2];
    const r = pc.push(g);
    const t = tMs / 1000;
    if (r && prevT != null) lag.addShift(prevT, t, r.dx, r.dy, r.peak);
    prevT = t;
    nFrames++; msSum += performance.now() - t0;
    if (tMs - lastReport > 500) {
      lastReport = tMs;
      const w = lag.update(poses, t);
      postMessage({ lag: lag.lagS, quality: lag.quality, scale: lag.scale, window: w, fps: nFrames * 2, ms: msSum / Math.max(1, nFrames) });
      nFrames = 0; msSum = 0;
    }
  } finally {
    frame.close?.();
  }
}

async function readStream(stream) {
  const reader = stream.getReader();
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    onFrame(value, now());
  }
}

onmessage = (e) => {
  const m = e.data;
  if (m.camera) setCamera(m.camera);
  if (m.poses) for (const [t, r, p, y] of m.poses) poses.push(t / 1000, r, p, y);
  if (m.stream) readStream(m.stream).catch((err) => postMessage({ error: String(err?.message ?? err) }));
  if (m.frame) onFrame(m.frame, m.t);
  if (m.reset) { pc.reset(); prevT = null; }
};
