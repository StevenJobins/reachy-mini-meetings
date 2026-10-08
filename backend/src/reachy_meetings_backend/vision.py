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
            running_mode=vision.RunningMode.VIDEO, num_faces=self.max_faces, output_face_blendshapes=True,
            min_face_detection_confidence=0.4, min_face_presence_confidence=0.4, min_tracking_confidence=0.4))

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
        import mediapipe as mp

        landmarker = self._landmarker()
        self.lock, self.latest = threading.Lock(), None
        threading.Thread(target=self._grab, daemon=True, name="camera").start()
        n, t_fps, t_ms, last_t = 0, time.time(), 0, 0.0
        while True:
            t0 = time.time()
            with self.lock:
                frame, t = self.latest or (None, 0.0)
            if frame is None or t == last_t:
                time.sleep(0.005)
                continue
            last_t = t
            small = cv2.cvtColor(cv2.resize(frame, (960, 540)), cv2.COLOR_BGR2RGB)
            t_ms = max(t_ms + 1, int(t * 1000))   # VIDEO mode wants increasing timestamps
            res = landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=small), t_ms)
            people = []
            for i, pts in enumerate(res.face_landmarks):
                xs, ys = [p.x for p in pts], [p.y for p in pts]
                x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
                if x1 - x0 < MIN_SIZE:
                    continue
                jaw = next((c.score for c in res.face_blendshapes[i] if c.category_name == "jawOpen"), 0.0)
                people.append({"cx": round((x0 + x1) / 2, 4), "cy": round((y0 + y1) / 2, 4), "top": round(y0, 4),
                               "w": round(x1 - x0, 4), "h": round(y1 - y0, 4), "mouth": round(jaw, 3)})
            publish(people, t)
            n += 1
            if t0 - t_fps >= 5:
                self.fps, n, t_fps = n / (t0 - t_fps), 0, t0
                log.info("Vision: %.1f detections/s", self.fps)
            time.sleep(max(0.0, self.period - (time.time() - t0)))   # cap the rate: the page needs no more
