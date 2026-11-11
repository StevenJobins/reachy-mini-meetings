"""Faces in the robot camera, found on this computer instead of the headset.

On the headset the detector ran at ~3 frames/s, too few to tell from mouth movement who is talking (it is
judged over 1.5 s). Here MediaPipe's FaceLandmarker gets ~54 frames/s at 960x540 (8 ms per frame, M-series
Mac, 2026-10-08), so the page gets the faces and mouth openness at --vision-hz and turns its own detector off.

The camera is read directly (macOS / Windows / Linux let a second app read it next to the daemon) and found
by name, not by index. Output per person, same as xr-client/pages/faces-worker.js: {cx, cy, top, w, h, mouth},
normalized 0..1 (origin top left), mouth = jawOpen 0..1. Time = wall clock of the capture.
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/"
             "face_landmarker.task")
MODEL_PATH = Path.home() / ".cache" / "reachy-meetings" / "face_landmarker.task"
MIN_SIZE = 0.015   # ignore tinier faces (fraction of the image width), as in faces-worker.js
# Dim rooms: below DARK (mean of the image, 0..255) the image is brightened (gamma) before the detection, and the
# detector accepts weaker faces. Measured on the robot in a dim room (brightness 41, one face, 60 frames over 12 s,
# 2026-11-11): face found in 5 frames as before (confidence 0.4, no gamma), 10 with gamma 0.5 alone, 2 with
# confidence 0.2 alone, 25 with both; CLAHE 4, full resolution 4.
DARK, GAMMA, CONFIDENCE = 90, 0.5, 0.2


def find_camera(name: str) -> int | None:
    """OpenCV index (with backend) of the first camera whose name contains `name`."""
    from cv2_enumerate_cameras import enumerate_cameras

    for cam in enumerate_cameras():
        if name.lower() in cam.name.lower():
            return cam.index
    return None


class FaceStream:
    def __init__(self, camera: str, hz: float = 20, max_faces: int = 6) -> None:
        self.camera, self.period, self.max_faces = camera, 1 / hz, max_faces
        self.fps = 0.0

    def _landmarker(self):
        from mediapipe.tasks.python import BaseOptions, vision

        if not MODEL_PATH.exists():
            MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
        return vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(MODEL_PATH)),
            running_mode=vision.RunningMode.IMAGE, num_faces=self.max_faces, output_face_blendshapes=True,
            min_face_detection_confidence=CONFIDENCE, min_face_presence_confidence=CONFIDENCE,
            min_tracking_confidence=0.4))

    def start(self, publish) -> None:
        """publish(people, t) is called from a background thread."""
        threading.Thread(target=self._run, args=(publish,), daemon=True, name="faces").start()

    def _grab(self) -> None:
        """Read the camera as fast as it delivers and keep only the newest frame: reading it at the
        detection rate let OpenCV queue up old frames (the faces would lag behind)."""
        import cv2

        cap = None
        while True:
            if cap is None:
                index = find_camera(self.camera)
                if index is None:
                    log.warning("Vision: no camera matching %r, retrying", self.camera)
                    time.sleep(5)
                    continue
                cap = cv2.VideoCapture(index)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
                log.info("Vision: faces from camera %r (index %d)", self.camera, index)
            ok, frame = cap.read()
            if not ok:
                log.warning("Vision: camera read failed, reopening")
                cap.release()
                cap = None
                time.sleep(2)
                continue
            with self.lock:
                self.latest = (frame, time.time())

    def _run(self, publish) -> None:
        import cv2

        finder = FaceFinder(self._landmarker())
        self.lock, self.latest = threading.Lock(), None
        threading.Thread(target=self._grab, daemon=True, name="camera").start()
        n, t_fps, last_t, seen = 0, time.time(), 0.0, 0
        while True:
            t0 = time.time()
            with self.lock:
                frame, t = self.latest or (None, 0.0)
            if frame is None or t == last_t:
                time.sleep(0.005)
                continue
            last_t = t
            people = finder.step(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            publish(people, t)
            n += 1
            seen += len(people)
            if t0 - t_fps >= 5:
                log.info("Vision: %.1f detections/s, %.1f faces per frame, brightness %.0f/255", n / (t0 - t_fps),
                         seen / n, float(frame[::16, ::16].mean()))
                self.fps, n, t_fps, seen = n / (t0 - t_fps), 0, t0, 0
            time.sleep(max(0.0, self.period - (time.time() - t0)))   # cap the rate: the page needs no more


# The detector inside the landmarker looks at the whole picture shrunk to its small input, so a face that is small
# in the wide-angle image (someone 2 m away, a face on a phone) is not found, whatever the camera resolution. So:
# the picture is searched piece by piece, one piece per frame (the whole, then six overlapping parts), and a face
# once found is followed in a close crop around it, where it is large: found more often, and the mouth is measured
# on many more pixels. Regions as fractions of the picture: x, y, width, height.
TILES = [(0.0, 0.0, 1.0, 1.0)] + [(x, y, 0.4, 0.6) for y in (0.0, 0.4) for x in (0.0, 0.3, 0.6)]
CROP = 2.4        # the crop around a followed face, in face sizes
LOST = 8          # frames without the face in its crop before it is given up
INPUT = 640       # longer side of what goes into the landmarker


class FaceFinder:
    """Faces in a frame (RGB), as vision.py publishes them. Needs a landmarker in IMAGE mode."""

    def __init__(self, landmarker) -> None:
        import numpy as np

        self.landmarker = landmarker
        self.brighten = np.clip(255 * (np.arange(256) / 255.0) ** GAMMA, 0, 255).astype(np.uint8)
        self.tracks: list[dict] = []   # {"face": the last answer, "missed": frames}
        self.k = 0

    def _detect(self, frame, x: float, y: float, w: float, h: float) -> list[dict]:
        import cv2
        import mediapipe as mp

        H, W = frame.shape[:2]
        x0, y0 = max(0, int(x * W)), max(0, int(y * H))
        x1, y1 = min(W, int((x + w) * W)), min(H, int((y + h) * H))
        if x1 - x0 < 16 or y1 - y0 < 16:
            return []
        crop = frame[y0:y1, x0:x1]
        scale = INPUT / max(crop.shape[:2])
        if scale < 1:
            crop = cv2.resize(crop, None, fx=scale, fy=scale)
        if crop[::4, ::4].mean() < DARK:
            crop = cv2.LUT(crop, self.brighten)
        res = self.landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=crop.copy()))
        faces = []
        for i, pts in enumerate(res.face_landmarks):
            xs = [(x0 + p.x * (x1 - x0)) / W for p in pts]
            ys = [(y0 + p.y * (y1 - y0)) / H for p in pts]
            fx0, fx1, fy0, fy1 = min(xs), max(xs), min(ys), max(ys)
            if fx1 - fx0 < MIN_SIZE:
                continue
            jaw = next((c.score for c in res.face_blendshapes[i] if c.category_name == "jawOpen"), 0.0)
            faces.append({"cx": round((fx0 + fx1) / 2, 4), "cy": round((fy0 + fy1) / 2, 4), "top": round(fy0, 4),
                          "w": round(fx1 - fx0, 4), "h": round(fy1 - fy0, 4), "mouth": round(jaw, 3)})
        return faces

    @staticmethod
    def _same(a: dict, b: dict) -> bool:
        return abs(a["cx"] - b["cx"]) < 0.6 * max(a["w"], b["w"]) and abs(a["cy"] - b["cy"]) < 0.6 * max(a["h"], b["h"])

    def step(self, frame) -> list[dict]:
        H, W = frame.shape[:2]
        people = []
        for tr in self.tracks:   # follow the known faces, each in a close crop
            f = tr["face"]
            side = CROP * max(f["w"] * W, f["h"] * H)
            found = self._detect(frame, f["cx"] - side / W / 2, f["cy"] - side / H / 2, side / W, side / H)
            found = [g for g in found if self._same(f, g)]
            if found:
                tr["face"], tr["missed"] = found[0], 0
                people.append(found[0])
            else:
                tr["missed"] += 1
        self.tracks = [tr for tr in self.tracks if tr["missed"] < LOST]
        tile = TILES[self.k % len(TILES)]   # and look for new ones in one piece of the picture
        self.k += 1
        for g in self._detect(frame, *tile):
            if not any(self._same(tr["face"], g) for tr in self.tracks):
                self.tracks.append({"face": g, "missed": 0})
                people.append(g)
        return people
