// Camera latency with a QR code. Two modes:
//  local:  this page shows the QR and decodes it (one computer, one clock, no sync).
//  remote: the QR runs on another screen (qr.html, e.g. the robot laptop) and this page (e.g. the headset) only
//          decodes; both clocks are synced to the caption backend (clocksync.js), accuracy shown as ± rtt/2.
// Local mode in detail:
// this page shows a QR code with the current time (ms) on every screen frame, Reachy's camera films the screen,
// and the same page decodes the QR in each received video frame.
//   latency = time the frame was presented in this page - time encoded in the QR
// That is: screen (QR shown) + camera exposure/readout + encode + network + jitter buffer + decode. Not included:
// showing the received frame on a display (+ ~1 screen frame for "glass to glass").
// DOM/media only, no three.js. QR: qrcode-generator (draw), BarcodeDetector or jsQR (decode).

const QR_LIB = "https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/+esm";
const JSQR_LIB = "https://cdn.jsdelivr.net/npm/jsqr@1.4.0/+esm";
const PREFIX = "RML:";          // local clock of this page
export const PREFIX_REMOTE = "RMB:";   // backend clock (qr.html, after clock sync)

/** Load the QR generator (qrcode-generator). */
export async function loadQrLib() { return (await import(QR_LIB)).default; }

/** Draw `text` as a QR code (version 2, with quiet zone) into canvas, at most maxPx wide. */
export function drawQrCode(canvas, qrLib, text, maxPx) {
  const q = qrLib(2, "M");
  q.addData(text);
  q.make();
  const n = q.getModuleCount(), quiet = 4;
  const px = Math.max(1, Math.floor(maxPx / (n + 2 * quiet)));
  if (canvas.width !== px * (n + 2 * quiet)) canvas.width = canvas.height = px * (n + 2 * quiet);
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#000";
  for (let r = 0; r < n; r++) for (let k = 0; k < n; k++) if (q.isDark(r, k)) ctx.fillRect((k + quiet) * px, (r + quiet) * px, px, px);
}

const quantile = (sorted, q) => sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))];

const ENOUGH = 300;                         // samples for a result (~10-20 s at 30 fps)
const HISTORY_KEY = "reachy-xr-latency";    // last results on this device

export function createLatencyTest({ video, clock = null, getEstimateS = () => null, getInfo = () => ({}), onActive = () => {}, log }) {
  let active = false, el = null, qr = null, decode = null, raf = 0, vfc = 0, mode = "local", syncTimer = 0;
  let samples = [], frames = 0, found = 0, lastLog = 0, result = null, decoderName = "?";
  // Chrome throttles a hidden or covered window (e.g. another app in front of it): the QR then changes only
  // ~once a second and the "latency" grows to seconds. So: watch how often the QR is redrawn and drop samples
  // while that is too slow (tested: a hidden test page reported ~2000 ms for a real 150 ms).
  let lastDraw = 0, drawGap = 0, throttled = false;
  const grab = document.createElement("canvas");
  const gctx = grab.getContext("2d", { willReadFrequently: true });
  const epoch = (perfMs) => performance.timeOrigin + perfMs;

  async function load() {
    if (!qr) qr = await loadQrLib();
    if (decode) return;
    if ("BarcodeDetector" in window && (await BarcodeDetector.getSupportedFormats()).includes("qr_code")) {
      const det = new BarcodeDetector({ formats: ["qr_code"] });
      decode = async (canvas) => (await det.detect(canvas))[0]?.rawValue ?? null;
      decoderName = "BarcodeDetector";
      log("latency: decoding with BarcodeDetector");
    } else {
      const jsQR = (await import(JSQR_LIB)).default;
      decode = async (canvas) => jsQR(gctx.getImageData(0, 0, canvas.width, canvas.height).data, canvas.width, canvas.height)?.data ?? null;
      decoderName = "jsQR";
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
        <div id="lat-help" style="color:#555"></div>
        <div id="lat-now" style="font:600 28px ui-monospace,monospace"></div>
        <div id="lat-stats" style="font:14px ui-monospace,monospace;white-space:pre-wrap;background:#f3f4f6;border-radius:10px;padding:10px"></div>
        <video id="lat-preview" muted playsinline style="width:100%;border-radius:10px;background:#000"></video>
        <div id="lat-warn" style="display:none;background:#fee2e2;color:#7f1d1d;border-radius:10px;padding:10px;font-weight:600">⚠ This window is covered or in the background: Chrome slows it down and the values would be wrong. Bring this window to the front and keep it uncovered (e.g. no chat window over it). Paused.</div>
        <div id="lat-done" style="display:none;background:#dcfce7;color:#14532d;border-radius:10px;padding:10px;font-weight:600"></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap"><button id="lat-copy" disabled>Copy result (Markdown)</button><button id="lat-reset">Reset</button><button id="lat-close">Close</button></div>
      </div>`;
    document.body.append(el);
    el.querySelector("#lat-close").onclick = close;
    el.querySelector("#lat-reset").onclick = () => { samples = []; frames = found = 0; result = null; showDone(); };
    el.querySelector("#lat-copy").onclick = async () => {
      if (!result) return;
      try { await navigator.clipboard.writeText(result.markdown); el.querySelector("#lat-copy").textContent = "Copied ✓"; }
      catch { prompt("Copy this line:", result.markdown); }
    };
    el.querySelector("#lat-help").textContent = mode === "local"
      ? "Point Reachy at this screen so the QR code fills a good part of its picture. Reachy holds still while this is open."
      : "Open qr.html on the computer next to Reachy (the robot laptop) and point Reachy at that screen. This device only reads the QR from the video; both clocks are synced to the caption backend. Reachy holds still while this is open.";
    el.querySelector("#lat-qr").style.display = mode === "local" ? "" : "none";
    const prev = el.querySelector("#lat-preview");   // what Reachy sees, to aim it
    prev.srcObject = video.srcObject; prev.play().catch(() => {});
  }

  /** New QR with the time of this screen frame. */
  function drawQr(t) {
    drawQrCode(el.querySelector("#lat-qr"), qr, `${PREFIX}${Math.round(epoch(t))}`, Math.min(innerHeight * 0.85, innerWidth * 0.5));
    el.querySelector("#lat-now").textContent = `${Math.round(epoch(t)) % 100000} ms`;
  }

  function loop(t) {
    if (!active) return;
    drawGap = lastDraw ? t - lastDraw : 0;
    lastDraw = t;
    if (mode === "local") drawQr(t);
    else el.querySelector("#lat-now").textContent = clock?.offset != null ? `clock sync ±${(clock.rtt / 2).toFixed(0)} ms` : "clock sync…";
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
    // throttled: the QR on screen is not refreshed every frame (gap > 50 ms), or the page is hidden
    throttled = document.visibilityState !== "visible" || (mode === "local" && (drawGap > 50 || performance.now() - lastDraw > 100));
    showWarning();
    if (throttled) return;
    decode?.(grab).then((text) => {
      let ms;
      if (mode === "local" && text?.startsWith(PREFIX)) ms = presented - Number(text.slice(PREFIX.length));
      else if (mode === "remote" && text?.startsWith(PREFIX_REMOTE) && clock?.offset != null)
        ms = presented + clock.offset - Number(text.slice(PREFIX_REMOTE.length));   // both in backend time
      else return;
      if (!(ms > 0 && ms < 5000)) return;
      found++;
      samples.push(ms);
      if (samples.length > 600) samples.shift();
      render();
    }).catch(() => {});
  }

  function showWarning() {
    if (!el) return;
    const w = el.querySelector("#lat-warn");
    w.style.display = throttled ? "block" : "none";
  }

  function showDone() {
    if (!el) return;
    const box = el.querySelector("#lat-done");
    box.style.display = result ? "block" : "none";
    box.textContent = result ? `✅ Enough values (${ENOUGH}): result saved. Copy it and paste it to Claude for docs/measurements/latency.md, or keep measuring.` : "";
    el.querySelector("#lat-copy").disabled = !result;
  }

  /** One result: stats + context, as JSON (saved on this device, logged) and as a Markdown table row. */
  function makeResult(s, mean, est) {
    const q = (x) => Math.round(quantile(s, x));
    const info = getInfo();
    const r = {
      date: new Date().toISOString().slice(0, 16).replace("T", " "),
      version: info.version ?? "?", median: q(0.5), p10: q(0.1), p90: q(0.9),
      min: Math.round(s[0]), max: Math.round(s[s.length - 1]), mean: Math.round(mean), n: s.length,
      readRate: frames ? Math.round(found / frames * 100) : 0,
      estimate: est != null ? Math.round(est * 1000) : null,
      video: info.video ?? "?", decoder: decoderName, mode,
      device: info.device ?? "?", syncMs: mode === "remote" && clock?.rtt != null ? Math.round(clock.rtt / 2) : null,
    };
    r.notes = mode === "remote" ? `${r.device}, QR on another screen (qr.html), clock sync ±${r.syncMs} ms` : `${r.device}, QR and reading on this device`;
    r.markdown = `| ${r.date} | ${r.version} | ${r.median} | ${r.p10}–${r.p90} | ${r.min}/${r.max} | ${r.n} | ${r.readRate} % | ${r.estimate ?? "–"} | ${r.video} | ${r.notes} |`;
    try {
      const h = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
      h.push(r); localStorage.setItem(HISTORY_KEY, JSON.stringify(h.slice(-20)));
    } catch {}
    log(`LATENCY_RESULT ${JSON.stringify(r)}`);
    return r;
  }

  function render() {
    if (!el || !samples.length) return;
    const s = [...samples].sort((a, b) => a - b), mean = s.reduce((a, b) => a + b, 0) / s.length;
    const est = getEstimateS();
    if (!result && s.length >= ENOUGH) { result = makeResult(s, mean, est); showDone(); }
    el.querySelector("#lat-stats").textContent = [
      `samples  ${s.length}${s.length < ENOUGH ? ` of ${ENOUGH}` : ""}  (QR read in ${found}/${frames} frames)`,
      `median   ${quantile(s, 0.5).toFixed(0)} ms`,
      `p10..p90 ${quantile(s, 0.1).toFixed(0)} .. ${quantile(s, 0.9).toFixed(0)} ms`,
      `min/max  ${s[0].toFixed(0)} / ${s[s.length - 1].toFixed(0)} ms   mean ${mean.toFixed(0)} ms`,
      est != null ? `page's estimate (reprojection) ${(est * 1000).toFixed(0)} ms` : "",
      mode === "remote" ? `clock sync with the backend: ${clock?.offset != null ? `±${(clock.rtt / 2).toFixed(0)} ms` : "not yet"}` : "",
    ].filter(Boolean).join("\n");
    if (performance.now() - lastLog > 5000) {
      lastLog = performance.now();
      log(`latency: median ${quantile(s, 0.5).toFixed(0)} ms, p10 ${quantile(s, 0.1).toFixed(0)}, p90 ${quantile(s, 0.9).toFixed(0)}, n ${s.length}, est ${est != null ? (est * 1000).toFixed(0) : "?"} ms`);
    }
  }

  /** mode "local": QR here; "remote": QR on another screen (qr.html), clocks synced via the backend. */
  async function open(m = "local") {
    if (active) return;
    if (!video.srcObject) { log("latency: no video yet (wake Reachy up first)"); return; }
    mode = m;
    if (mode === "remote" && !clock) { log("latency: no clock sync available"); return; }
    await load();
    build();
    if (mode === "remote") {   // sync now and every 15 s (clocks drift slowly; the best round wins)
      clock.sync().then((r) => { if (!r) log("latency: clock sync failed (caption backend connected?)"); });
      syncTimer = setInterval(() => clock.sync(), 15000);
    }
    active = true; samples = []; frames = found = 0; result = null;
    onActive(true);
    raf = requestAnimationFrame(loop);
    vfc = video.requestVideoFrameCallback(onFrame);
    log("latency: test open");
  }

  function close() {
    if (!active) return;
    active = false;
    cancelAnimationFrame(raf);
    clearInterval(syncTimer);
    video.cancelVideoFrameCallback?.(vfc);
    el?.remove(); el = null;
    onActive(false);
    log("latency: test closed");
  }

  return {
    open, close,
    get active() { return active; },
    get samples() { return [...samples]; },
    /** Results saved on this device (newest last). */
    get history() { try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]"); } catch { return []; } },
  };
}
