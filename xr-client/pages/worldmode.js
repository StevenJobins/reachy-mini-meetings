// World view mode (Dominic): glue between the app, the lag worker, the paint policy and the panorama renderer.
// Switch: Settings -> VR view -> "World view", or More -> 🧭 in VR. Off: nothing of this runs (no worker, no GPU work).
//
// Per VR frame (frame()): the live picture is shown at the head pose of its CAPTURE time, pose(t_frame - lag), with
// the lag measured online from the picture itself (videolag-worker.js); sharp frames taken while the head was still
// are painted into the panorama (worldview.js) at that pose. The room frame is the app's (robot world turned by the
// speaker-following base), exactly like Simon's room panorama.

import { createWorldView } from "./worldview.js";
import { PoseHistory } from "./videolag.js";
import { PaintPolicy, LookAround } from "./worldpolicy.js";

const DRAW_DELAY_S = 0.008;   // frame arrival (worker) -> drawn on the live texture: ~half a VR frame
const SMALL_W = 128, SMALL_H = 64;

export function createWorldMode({ scene, video, camera, log = console.log, panoWidth = 2048, fixedLagS = null, workerFrames = "auto", exposure = true }) {
  let active = false, view = null, worker = null, workerTrack = null, workerMode = "-", cam = camera;
  const poses = new PoseHistory(15);
  const toWorker = [];
  const lag = { lagS: null, quality: 0, scale: null, window: null, fps: 0, ms: 0, error: null };
  const policy = new PaintPolicy();
  const look = new LookAround();
  let lastSeq = -1, seqT = 0, probeFor = null, live = false;
  let jsMs = 0, jsN = 0, jsAvg = 0, paintMs = 0;
  const epochMs = (tS) => performance.timeOrigin + tS * 1000;

  function pxPerDeg() {
    return { pxPerDegX: cam.fx * SMALL_W / cam.width * Math.PI / 180, pxPerDegY: cam.fy * SMALL_H / cam.height * Math.PI / 180 };
  }

  function startWorker() {
    if (worker) return;
    try {
      worker = new Worker(new URL(`./videolag-worker.js${new URL(import.meta.url).search}`, import.meta.url), { type: "module" });
    } catch (e) { log("world view: no lag worker", e?.message ?? e); return; }
    worker.onmessage = (e) => {
      const m = e.data;
      if (m.error) { lag.error = m.error; log("world view: lag worker", m.error); return; }
      const before = lag.lagS;
      const { lag: lagS, ...rest } = m;
      Object.assign(lag, rest, { lagS });
      if (m.lag != null && (before == null || Math.abs(m.lag - before) > 0.03)) {
        log(`world view: video lag ${(m.lag * 1000).toFixed(0)} ms (r2 ${m.quality.toFixed(2)}, scale ${m.scale?.toFixed(2)}, ${m.fps} fps analysed, ${m.ms.toFixed(1)} ms/frame in the worker)`);
      }
    };
    worker.postMessage({ camera: pxPerDeg() });
  }

  // Frames for the worker: straight from the WebRTC track (MediaStreamTrackProcessor, stream transferred to the
  // worker: zero main-thread work); fallback: a small ImageBitmap of the live canvas per new frame.
  function feedWorker() {
    if (!active || !worker) return;
    if (workerFrames === "bitmap") { workerMode = "bitmap"; return; }
    const track = video.srcObject?.getVideoTracks?.()[0];
    if (!track || track.readyState !== "live" || track.id === workerTrack) return;
    workerTrack = track.id;
    if ("MediaStreamTrackProcessor" in window) {
      try {
        const readable = new MediaStreamTrackProcessor({ track: track.clone() }).readable;
        worker.postMessage({ stream: readable, reset: true }, [readable]);
        workerMode = "track";
        log("world view: lag worker reads the video track");
        return;
      } catch (e) { log("world view: track -> worker failed, using canvas copies:", e?.message ?? e); }
    }
    workerMode = "bitmap";
  }
  setInterval(feedWorker, 1000);
  setInterval(() => {
    if (!worker || !toWorker.length) return;
    worker.postMessage({ poses: toWorker.splice(0) });
  }, 100);

  function ensureView() {
    if (view) return view;
    view = createWorldView({ renderer: scene.three.renderer, parent: scene.three.scene, camera: cam, log, width: panoWidth, exposure });
    log(`world view: panorama ${panoWidth}x${panoWidth / 2}`);
    return view;
  }

  return {
    /** Measured head pose (app.js onMeasuredHead), t in s of performance.now(). */
    pushPose(t, roll, pitch, yaw) {
      poses.push(t, roll, pitch, yaw);
      if (active) toWorker.push([epochMs(t), roll, pitch, yaw]);
    },

    setActive(on) {
      if (on === active) return;
      active = on;
      if (on) { ensureView(); startWorker(); workerTrack = null; feedWorker(); }
      else { scene.setWorldView(false); look.cancel(); }
      if (view) view.visible = on;
      log(`world view ${on ? "on" : "off"}`);
    },
    get active() { return active; },

    setCamera(c) { cam = c; view?.setCamera(c); worker?.postMessage({ camera: pxPerDeg() }); },

    /** The lag in use (s): the measured one, else the app's fallback (WebRTC stats). */
    lagS(fallbackS) { return fixedLagS ?? lag.lagS ?? fallbackS; },
    /** Estimator state (harness / log). */
    get lag() { return { ...lag }; },
    /** The panorama renderer (harness). */
    get view() { return view; },

    /**
     * Per VR frame. roomQ: room frame in XR; follow: the robot does what is commanded (app's sanity check).
     * Returns the capture-time head pose of the live picture [roll, pitch, yaw] (for the bubbles), or null.
     */
    frame(nowS, roomQ, fallbackLagS, follow, visible = true) {
      if (!active || !view) return null;
      const t0 = performance.now();
      view.group.quaternion.set(roomQ.x, roomQ.y, roomQ.z, roomQ.w);
      view.visible = visible;
      const tex = scene.videoTexture, seq = scene.videoFrameSeq;
      if (seq !== lastSeq) {
        lastSeq = seq; seqT = nowS - DRAW_DELAY_S;
        if (workerMode === "bitmap" && worker && tex) {
          if (toWorker.length) worker.postMessage({ poses: toWorker.splice(0) });   // poses first: the fit needs them
          createImageBitmap(scene.videoFrame(), { resizeWidth: SMALL_W, resizeHeight: SMALL_H })
            .then((bm) => worker.postMessage({ frame: bm, t: epochMs(seqT) }, [bm])).catch(() => {});
        }
      }
      const measured = fixedLagS ?? lag.lagS;   // fixed: ?lag= (debugging), else measured
      const lagUsed = measured ?? fallbackLagS;
      const pose = poses.length ? poses.at(seqT - lagUsed) : null;
      live = !!(follow && tex && pose);
      scene.setWorldView(live);
      view.setLive(live ? tex : null, pose, nowS);
      let painted = false;
      if (live && visible) {
        const d = policy.decide(seqT, poses, measured, seq);
        if (d) {
          // exposure: measure this view against the panorama first, paint once that gain is back (next frames)
          const probed = probeFor && Math.hypot(d.pose[1] - probeFor.pose[1], d.pose[2] - probeFor.pose[2]) < 2 && nowS - probeFor.t < 1;
          if (view.stats.paints > 0 && !probed) {
            if (!view.probeBusy && view.probe(tex, d.pose)) probeFor = { pose: d.pose, t: nowS };
          } else if (view.stats.paints === 0 || !view.probeBusy || nowS - probeFor.t > 0.5) {   // gain for this view is back
            const r = view.paint(tex, d.pose, nowS, d.still);
            policy.painted(seqT, d.pose, seq);
            paintMs = r.ms; painted = true; probeFor = null;
            const st = look.onPaint(nowS, d.pose);
            if (st === "done") log(`world view: look around done (${look.log.map((x) => `${x.key} ${x.s}s${x.how === "timeout" ? " timeout" : ""}`).join(", ")})`);
          }
        }
      }
      if (look.active && look.step(nowS) === "done") log("world view: look around done (last stop timed out)");
      if (!painted) { jsMs += performance.now() - t0; jsN++; }
      if (jsN >= 120) { jsAvg = jsMs / jsN; jsMs = 0; jsN = 0; }
      return live ? pose : null;
    },

    /** Look around once (all stops), pausing the head mirroring (app's scanTarget path). */
    lookAround() { if (!active) return; look.start(performance.now() / 1000); log("world view: look around"); },
    lookTarget() { return active && look.active ? look.target : null; },

    /** One status line. */
    status() {
      if (!active) return "";
      const s = view?.stats ?? {};
      const l = lag.lagS != null ? `${(lag.lagS * 1000).toFixed(0)}ms r2 ${lag.quality.toFixed(2)}` : `? (${lag.window ? `motion ${lag.window.motion.toFixed(0)}°/s r2 ${lag.window.r2.toFixed(2)}` : "no data"})`;
      return `wv lag ${l} [${workerMode} ${lag.fps}fps ${lag.ms.toFixed(1)}ms] paints ${s.paints ?? 0} (${paintMs.toFixed(1)}ms, ${policy.reason}) gain ${(s.gain ?? 1).toFixed(2)} js ${jsAvg.toFixed(2)}ms${look.active ? ` look ${look.progress}` : ""}`;
    },
  };
}
