// People in the robot camera image, for placing speech bubbles above the speaker's head.
// The detection (MediaPipe pose + face, see faces-worker.js) runs in a Web Worker on small frames
// (640x360), so the render loop is never blocked. DOM/media only, no three.js.
// If the worker fails, onFaces is simply never called and the bubbles use the mic direction.

const SIZE = { resizeWidth: 640, resizeHeight: 360, resizeQuality: "low" };

/**
 * getSource() -> canvas / video with the current camera frame, or null to pause.
 * onFaces([{cx, cy, top, w, h, mouth}], tSeconds): head box per person, normalized (0..1, origin top left),
 *   top < 0 = head above the image; mouth = jawOpen 0..1 or null (no face measured this frame).
 */
export function createFaces({ getSource, onFaces, log, hz = 8 }) {
  let state = "loading", busy = false, n = 0, fps = 0, seen = 0;
  setInterval(() => { fps = n; n = 0; }, 1000);

  const worker = new Worker(new URL("./faces-worker.js", import.meta.url));
  worker.onmessage = ({ data }) => {
    if (data.ready) { state = "on"; log("people: detector ready (worker)"); return; }
    if (data.error && !data.people) {
      busy = false;
      if (state === "loading") { state = "off"; log("faces: unavailable,", data.error); }
      return;
    }
    busy = false;
    n++;
    seen = data.people.length;
    onFaces(data.people, data.ts / 1000);
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

  return { stats: () => (state === "on" ? `people ${seen} @ ${fps} fps` : `people ${state}`) };
}
