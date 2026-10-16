// Camera latency, measured with a QR loop on one computer (no clock sync needed):
// this page shows a QR code with the current time (ms) on every screen frame, Reachy's camera films the screen,
// and the same page decodes the QR in each received video frame.
//   latency = time the frame was presented in this page - time encoded in the QR
// That is: screen (QR shown) + camera exposure/readout + encode + network + jitter buffer + decode. Not included:
// showing the received frame on a display (+ ~1 screen frame for "glass to glass").
// DOM/media only, no three.js. QR: qrcode-generator (draw), BarcodeDetector or jsQR (decode).

const QR_LIB = "https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/+esm";
const JSQR_LIB = "https://cdn.jsdelivr.net/npm/jsqr@1.4.0/+esm";
const PREFIX = "RML:";   // "Reachy Meetings latency"

const quantile = (sorted, q) => sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))];

export function createLatencyTest({ video, getEstimateS = () => null, onActive = () => {}, log }) {
  let active = false, el = null, qr = null, decode = null, raf = 0, vfc = 0;
  let samples = [], frames = 0, found = 0, lastLog = 0;
  const grab = document.createElement("canvas");
  const gctx = grab.getContext("2d", { willReadFrequently: true });
  const epoch = (perfMs) => performance.timeOrigin + perfMs;

  async function load() {
    if (!qr) qr = (await import(QR_LIB)).default;
    if (decode) return;
    if ("BarcodeDetector" in window && (await BarcodeDetector.getSupportedFormats()).includes("qr_code")) {
      const det = new BarcodeDetector({ formats: ["qr_code"] });
      decode = async (canvas) => (await det.detect(canvas))[0]?.rawValue ?? null;
      log("latency: decoding with BarcodeDetector");
    } else {
      const jsQR = (await import(JSQR_LIB)).default;
      decode = async (canvas) => jsQR(gctx.getImageData(0, 0, canvas.width, canvas.height).data, canvas.width, canvas.height)?.data ?? null;
      log("latency: decoding with jsQR");
    }
  }

  function build() {
    el = document.createElement("div");
    el.style.cssText = "position:fixed;inset:0;z-index:1000;background:#fff;color:#111;display:flex;gap:24px;align-items:center;justify-content:center;padding:16px;font:15px system-ui,sans-serif";
    el.innerHTML = `
      <canvas id="lat-qr" style="image-rendering:pixelated;flex:none"></canvas>
      <div style="display:grid;gap:10px;max-width:360px">
        <div style="font-size:20px;font-weight:650">Camera latency</div>
        <div style="color:#555">Point Reachy at this screen so the QR code fills a good part of its picture. Reachy holds still while this is open.</div>
        <div id="lat-now" style="font:600 28px ui-monospace,monospace"></div>
        <div id="lat-stats" style="font:14px ui-monospace,monospace;white-space:pre-wrap;background:#f3f4f6;border-radius:10px;padding:10px"></div>
        <video id="lat-preview" muted playsinline style="width:100%;border-radius:10px;background:#000"></video>
        <div style="display:flex;gap:8px"><button id="lat-reset">Reset</button><button id="lat-close">Close</button></div>
      </div>`;
    document.body.append(el);
    el.querySelector("#lat-close").onclick = close;
    el.querySelector("#lat-reset").onclick = () => { samples = []; frames = found = 0; };
    const prev = el.querySelector("#lat-preview");   // what Reachy sees, to aim it
    prev.srcObject = video.srcObject; prev.play().catch(() => {});
  }

  /** New QR with the time of this screen frame. */
  function drawQr(t) {
    const q = qr(2, "M");
    q.addData(`${PREFIX}${Math.round(epoch(t))}`);
    q.make();
    const n = q.getModuleCount(), quiet = 4;
    const c = el.querySelector("#lat-qr");
    const px = Math.floor(Math.min(innerHeight * 0.85, innerWidth * 0.5) / (n + 2 * quiet));
    if (c.width !== px * (n + 2 * quiet)) c.width = c.height = px * (n + 2 * quiet);
    const ctx = c.getContext("2d");
    ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, c.width, c.height);
    ctx.fillStyle = "#000";
    for (let r = 0; r < n; r++) for (let k = 0; k < n; k++) if (q.isDark(r, k)) ctx.fillRect((k + quiet) * px, (r + quiet) * px, px, px);
    el.querySelector("#lat-now").textContent = `${Math.round(epoch(t)) % 100000} ms`;
  }

  function loop(t) {
    if (!active) return;
    drawQr(t);
    raf = requestAnimationFrame(loop);
  }

  /** Every received video frame: copy it right away (so the decoded QR belongs to this frame), then decode. */
  function onFrame(_now, meta) {
    if (!active) return;
    vfc = video.requestVideoFrameCallback(onFrame);
    frames++;
    const presented = epoch(meta.presentationTime ?? performance.now());
    const w = Math.min(960, video.videoWidth), h = Math.round(w * video.videoHeight / video.videoWidth);
    if (!w || !h) return;
    if (grab.width !== w || grab.height !== h) { grab.width = w; grab.height = h; }
    gctx.drawImage(video, 0, 0, w, h);
    decode?.(grab).then((text) => {
      if (!text?.startsWith(PREFIX)) return;
      const ms = presented - Number(text.slice(PREFIX.length));
      if (!(ms > 0 && ms < 5000)) return;
      found++;
      samples.push(ms);
      if (samples.length > 600) samples.shift();
      render();
    }).catch(() => {});
  }

  function render() {
    if (!el || !samples.length) return;
    const s = [...samples].sort((a, b) => a - b), mean = s.reduce((a, b) => a + b, 0) / s.length;
    const est = getEstimateS();
    el.querySelector("#lat-stats").textContent = [
      `samples  ${s.length}  (QR read in ${found}/${frames} frames)`,
      `median   ${quantile(s, 0.5).toFixed(0)} ms`,
      `p10..p90 ${quantile(s, 0.1).toFixed(0)} .. ${quantile(s, 0.9).toFixed(0)} ms`,
      `min/max  ${s[0].toFixed(0)} / ${s[s.length - 1].toFixed(0)} ms   mean ${mean.toFixed(0)} ms`,
      est != null ? `page's estimate (reprojection) ${(est * 1000).toFixed(0)} ms` : "",
    ].filter(Boolean).join("\n");
    if (performance.now() - lastLog > 5000) {
      lastLog = performance.now();
      log(`latency: median ${quantile(s, 0.5).toFixed(0)} ms, p10 ${quantile(s, 0.1).toFixed(0)}, p90 ${quantile(s, 0.9).toFixed(0)}, n ${s.length}, est ${est != null ? (est * 1000).toFixed(0) : "?"} ms`);
    }
  }

  async function open() {
    if (active) return;
    if (!video.srcObject) { log("latency: no video yet (wake Reachy up first)"); return; }
    await load();
    build();
    active = true; samples = []; frames = found = 0;
    onActive(true);
    raf = requestAnimationFrame(loop);
    vfc = video.requestVideoFrameCallback(onFrame);
    log("latency: test open");
  }

  function close() {
    if (!active) return;
    active = false;
    cancelAnimationFrame(raf);
    video.cancelVideoFrameCallback?.(vfc);
    el?.remove(); el = null;
    onActive(false);
    log("latency: test closed");
  }

  return { open, close, get active() { return active; }, get samples() { return [...samples]; } };
}
