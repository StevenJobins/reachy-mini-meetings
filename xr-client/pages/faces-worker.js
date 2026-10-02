// Web Worker for faces.js: runs MediaPipe FaceLandmarker off the main thread, so rendering never waits.
// Classic worker (MediaPipe's wasm loader needs importScripts), the library itself comes in via import().
// In:  {bitmap: ImageBitmap, ts: ms}   Out: {faces: [{cx, cy, top, w, h, mouth}], ts} | {ready} | {error}

const MP = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.0.1";
const MODEL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task";

let landmarker = null;

(async () => {
  try {
    const { FilesetResolver, FaceLandmarker } = await import(`${MP}/vision_bundle.mjs`);
    const files = await FilesetResolver.forVisionTasks(`${MP}/wasm`);
    const options = (delegate) => ({
      baseOptions: { modelAssetPath: MODEL, delegate },
      runningMode: "VIDEO", numFaces: 4, outputFaceBlendshapes: true,
      // lower than the 0.5 defaults: people close to the robot are often cut off at the image edge
      minFaceDetectionConfidence: 0.3, minFacePresenceConfidence: 0.3, minTrackingConfidence: 0.3,
    });
    landmarker = await FaceLandmarker.createFromOptions(files, options("GPU"))
      .catch(() => FaceLandmarker.createFromOptions(files, options("CPU")));
    postMessage({ ready: true });
  } catch (e) {
    postMessage({ error: String(e?.message ?? e) });
  }
})();

onmessage = ({ data: { bitmap, ts } }) => {
  if (!landmarker) { bitmap.close(); postMessage({ faces: [], ts }); return; }
  let res;
  try { res = landmarker.detectForVideo(bitmap, ts); } catch (e) { bitmap.close(); postMessage({ error: String(e?.message ?? e), ts }); return; }
  bitmap.close();
  postMessage({
    ts,
    faces: res.faceLandmarks.map((pts, i) => {
      let x0 = 1, x1 = 0, y0 = 1, y1 = 0;
      for (const p of pts) {
        if (p.x < x0) x0 = p.x; if (p.x > x1) x1 = p.x;
        if (p.y < y0) y0 = p.y; if (p.y > y1) y1 = p.y;
      }
      const jaw = res.faceBlendshapes[i]?.categories.find((c) => c.categoryName === "jawOpen")?.score ?? 0;
      return { cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, top: y0, w: x1 - x0, h: y1 - y0, mouth: jaw };
    }),
  });
};
