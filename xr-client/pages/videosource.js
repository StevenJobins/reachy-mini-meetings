// Camera frames for the VR video window. DOM + media only, no three.js.
//
// Why not just upload the <video> element as a texture: a plain THREE.Texture is allocated once with
// texStorage2D at the size of the FIRST frame. WebRTC starts small (often with a black frame) and then
// raises the resolution, so every later frame failed to upload and VR stayed black while the page
// video was fine. On top of that, Android may stop rendering the <video> element while an immersive
// session hides the page.
//
// So frames are drawn into a canvas of FIXED size, and that canvas is the texture. Modes:
//   track  - read VideoFrames straight from the WebRTC track (MediaStreamTrackProcessor), independent
//            of the <video> element. Default where supported.
//   canvas - draw the <video> element into the canvas.
//   direct - upload the <video> element itself (THREE.VideoTexture in scene.js). Old path, for comparison.

const W = 1920, H = 1080;  // the camera's full resolution (1280x720 looked soft); fixed so the GPU texture never changes size
const MODE_KEY = "reachy-xr-video-mode";
// At most this many uploads of the 1080p frame per second: the camera sends 60 fps, and uploading every one of
// them (plus decoding) dragged VR down to ~19 fps. Limiting it in the daemon (videorate) made WebRTC sessions
// hang mid-negotiation, so it is done here.
const MAX_DRAW_FPS = 30;

export function createVideoSource(video, log) {
  const modes = ["track", "canvas", "direct"].filter((m) => m !== "track" || "MediaStreamTrackProcessor" in window);
  let mode = modes[0];
  try { const saved = localStorage.getItem(MODE_KEY); if (modes.includes(saved)) mode = saved; } catch {}

  const canvas = document.createElement("canvas");
  canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext("2d", { alpha: false });

  let reader = null, trackId = null, latest = null, fresh = false, hasFrame = false;
  let lastTrackFrame = 0, restarts = 0, usingFallback = false;
  let received = 0, drawn = 0, rxFps = 0, drawFps = 0, size = "-", lastDraw = 0;
  setInterval(() => { rxFps = received; drawFps = drawn; received = drawn = 0; }, 1000);

  function stopTrack() {
    const r = reader;
    reader = null; trackId = null;
    r?.cancel().catch(() => {});
    latest?.close(); latest = null; fresh = false;
  }

  async function readTrack(track) {
    trackId = track.id;
    lastTrackFrame = performance.now();
    const clone = track.clone();   // own sink, does not disturb the page <video>
    const r = new MediaStreamTrackProcessor({ track: clone }).readable.getReader();
    reader = r;
    log("video: reading frames from track", track.id.slice(0, 8));
    try {
      for (;;) {
        const { value, done } = await r.read();
        if (done || reader !== r) { value?.close(); break; }
        latest?.close();
        latest = value; fresh = true; received++; lastTrackFrame = performance.now();
      }
    } catch (e) {
      log("video track reader:", e?.message ?? e);
    }
    clone.stop();
    if (reader === r) { reader = null; trackId = null; }
  }

  function draw(src, w, h) {
    if (!w || !h) return false;
    ctx.drawImage(src, 0, 0, W, H);
    lastDraw = performance.now();
    size = `${w}x${h}`;
    drawn++; hasFrame = true;
    return true;
  }

  return {
    canvas,
    get mode() { return mode; },
    /** True once the canvas holds a camera frame (in track/canvas mode). */
    get hasFrame() { return hasFrame; },

    /** Next mode (VR button), remembered on this device. */
    cycle() {
      mode = modes[(modes.indexOf(mode) + 1) % modes.length];
      if (mode !== "track") stopTrack();
      hasFrame = false;
      try { localStorage.setItem(MODE_KEY, mode); } catch {}
      log("video mode:", mode);
      return mode;
    },

    /** Call once per rendered frame. Returns true when the canvas got a new image. */
    update() {
      if (mode === "track") {
        const track = video.srcObject?.getVideoTracks?.()[0];
        if (track && track.readyState === "live" && track.id !== trackId) { stopTrack(); readTrack(track); }
        if (fresh && latest && performance.now() - lastDraw >= 1000 / MAX_DRAW_FPS - 2) {
          fresh = false;
          usingFallback = false;
          return draw(latest, latest.displayWidth, latest.displayHeight);
        }
        // Watchdog: a reader can stall without an error (e.g. after a crash or reconnect). Bridge the gap
        // with the <video> element, and restart the reader after 3 s without a frame.
        const silentMs = performance.now() - lastTrackFrame;
        if (trackId && silentMs > 3000) {
          restarts++;
          log("video: no frame from track for 3 s, restarting reader");
          stopTrack();   // trackId = null -> next update() starts a new reader
          lastTrackFrame = performance.now();
        }
        if (silentMs > 1000 && video.readyState >= video.HAVE_CURRENT_DATA && video.videoWidth) {
          usingFallback = true;
          return draw(video, video.videoWidth, video.videoHeight);
        }
        return false;
      }
      if (mode === "canvas") {
        if (video.readyState < video.HAVE_CURRENT_DATA || !video.videoWidth) return false;
        size = `${video.videoWidth}x${video.videoHeight}`;
        return draw(video, video.videoWidth, video.videoHeight);
      }
      if (video.videoWidth) size = `${video.videoWidth}x${video.videoHeight}`;
      return false;
    },

    /** One status line for the HUD. */
    stats() {
      const rx = mode === "track" ? `rx ${rxFps} fps${usingFallback ? " (fallback <video>)" : ""}${restarts ? ` restarts ${restarts}` : ""}  ` : "";
      const tex = mode === "direct" ? "tex <video>" : `tex ${drawFps} fps`;
      return `video mode ${mode}   ${rx}${tex}   src ${size}`;
    },
  };
}
