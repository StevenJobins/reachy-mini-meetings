// Faces in the robot camera image, for placing speech bubbles above the speaker's head.
// The detection (MediaPipe FaceLandmarker) runs in faces-worker.js on small frames (640x360), so the
// render loop is never blocked. DOM/media only, no three.js.
// If the worker fails, onFaces is simply never called and the bubbles fall back to direction / subtitles.

const SIZE = { resizeWidth: 640, resizeHeight: 360, resizeQuality: "low" };

/**
 * getSource() -> canvas / video with the current camera frame, or null to pause.
 * onFaces([{cx, cy, top, w, h, mouth}], tSeconds): normalized box (0..1, origin top left), mouth = jawOpen 0..1.
 */
export function createFaces({ getSource, onFaces, log, hz = 8 }) {
  let state = "loading", busy = false, n = 0, fps = 0, seen = 0;
  setInterval(() => { fps = n; n = 0; }, 1000);

  const worker = new Worker(new URL("./faces-worker.js", import.meta.url));
  worker.onmessage = ({ data }) => {
    if (data.ready) { state = "on"; log("faces: detector ready (worker)"); return; }
    if (data.error && !data.faces) {
      busy = false;
      if (state === "loading") { state = "off"; log("faces: unavailable,", data.error); }
      return;
    }
    busy = false;
    n++;
    seen = data.faces.length;
    onFaces(data.faces, data.ts / 1000);
  };
  worker.onerror = (e) => { state = "off"; log("faces: worker failed,", e.message); };

  setInterval(async () => {
    if (state !== "on" || busy) return;   // one frame in flight at a time: never queue up
    const src = getSource();
    if (!src || src.videoWidth === 0) return;
    busy = true;
    try {
      const bitmap = await createImageBitmap(src, SIZE);
      worker.postMessage({ bitmap, ts: performance.now() }, [bitmap]);
    } catch { busy = false; }
  }, 1000 / hz);

  return { stats: () => (state === "on" ? `faces ${seen} @ ${fps} fps` : `faces ${state}`) };
}
