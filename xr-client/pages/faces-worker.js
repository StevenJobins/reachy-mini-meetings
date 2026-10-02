// Web Worker for faces.js: finds the people in the camera image off the main thread, so rendering never waits.
// Classic worker (MediaPipe's wasm loader needs importScripts), the library itself comes in via import().
//
// Only faces count as speakers (hands, thumbs or backs must never get a bubble):
//   FaceLandmarker (every frame)  -> face box + mouth openness (jawOpen): the main source
//   PoseLandmarker (every 2nd)    -> adds heads the face model misses (profile, partly turned away), but
//                                    only when nose and an eye are clearly visible, i.e. it is a face
// In:  {bitmap: ImageBitmap, ts: ms}
// Out: {people: [{cx, cy, top, w, h, mouth}], ts} (normalized 0..1, top may be < 0 = above the image;
//      mouth = jawOpen 0..1, or null when no face was measured this frame) | {ready} | {error}

const MP = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.0.1";
const MODELS = "https://storage.googleapis.com/mediapipe-models";
const FACE_MODEL = `${MODELS}/face_landmarker/face_landmarker/float16/1/face_landmarker.task`;
const POSE_MODEL = `${MODELS}/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task`;
const VIS = 0.7;      // pose landmark counts as seen above this visibility
const MIN_SIZE = 0.035;   // ignore tiny detections (fraction of the image width)

let pose = null, face = null, frame = 0;

(async () => {
  try {
    const { FilesetResolver, FaceLandmarker, PoseLandmarker } = await import(`${MP}/vision_bundle.mjs`);
    const files = await FilesetResolver.forVisionTasks(`${MP}/wasm`);
    const make = (Task, model, extra) => (delegate) => Task.createFromOptions(files, {
      baseOptions: { modelAssetPath: model, delegate }, runningMode: "VIDEO", ...extra,
    });
    const withFallback = (create) => create("GPU").catch(() => create("CPU"));
    [pose, face] = await Promise.all([
      withFallback(make(PoseLandmarker, POSE_MODEL, { numPoses: 4 })),
      withFallback(make(FaceLandmarker, FACE_MODEL, { numFaces: 4, outputFaceBlendshapes: true })),
    ]);
    postMessage({ ready: true });
  } catch (e) {
    postMessage({ error: String(e?.message ?? e) });
  }
})();

const mean = (v) => v.reduce((a, b) => a + b, 0) / v.length;

/** Head box from the 33 pose landmarks, only when a face is clearly visible (nose + an eye), else null. */
function headFromPose(lm) {
  const seen = (i) => lm[i].visibility > VIS && lm[i].x > 0 && lm[i].x < 1 && lm[i].y > 0 && lm[i].y < 1;
  if (!seen(0) || !(seen(2) || seen(5))) return null;
  const head = [0, 2, 5, 7, 8].filter(seen).map((i) => lm[i]);   // nose, eyes, ears
  const cx = mean(head.map((p) => p.x)), cy = mean(head.map((p) => p.y));
  const shoulderW = lm[11].visibility > VIS && lm[12].visibility > VIS ? Math.abs(lm[11].x - lm[12].x) : 0;
  const size = seen(7) && seen(8) ? Math.abs(lm[7].x - lm[8].x) * 1.4 : Math.max(shoulderW * 0.45, 0.06);
  if (size < MIN_SIZE) return null;
  return { cx, cy, top: cy - size * 0.75, w: size, h: size * 1.4, mouth: null };
}

function boxFromFace(pts) {
  let x0 = 1, x1 = 0, y0 = 1, y1 = 0;
  for (const p of pts) {
    if (p.x < x0) x0 = p.x; if (p.x > x1) x1 = p.x;
    if (p.y < y0) y0 = p.y; if (p.y > y1) y1 = p.y;
  }
  return { cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, top: y0, w: x1 - x0, h: y1 - y0 };
}

/** The pose model sometimes reports one person twice: keep the bigger head when two are side by side. */
function dedupe(people) {
  people.sort((a, b) => b.w - a.w);
  return people.filter((p, i) => !people.slice(0, i).some((q) => Math.abs(q.cx - p.cx) < 0.6 * q.w + 0.03));
}

onmessage = ({ data: { bitmap, ts } }) => {
  if (!pose) { bitmap.close(); postMessage({ people: [], ts }); return; }
  try {
    const people = frame++ % 2 === 0
      ? dedupe(pose.detectForVideo(bitmap, ts).landmarks.map(headFromPose).filter(Boolean)) : [];
    {
      const res = face.detectForVideo(bitmap, ts);
      res.faceLandmarks.forEach((pts, i) => {
        const box = boxFromFace(pts);
        if (box.w < MIN_SIZE) return;
        const mouth = res.faceBlendshapes[i]?.categories.find((c) => c.categoryName === "jawOpen")?.score ?? 0;
        // the face belongs to the person whose estimated head is closest; its box is the better one
        let best = null, bestD = 0.15;
        for (const p of people) {
          const d = Math.hypot(p.cx - box.cx, p.cy - box.cy);
          if (d < bestD) { bestD = d; best = p; }
        }
        if (best) Object.assign(best, box, { mouth });
        else people.push({ ...box, mouth });
      });
    }
    postMessage({ people, ts });
  } catch (e) {
    postMessage({ error: String(e?.message ?? e), ts });
  } finally {
    bitmap.close();
  }
};
