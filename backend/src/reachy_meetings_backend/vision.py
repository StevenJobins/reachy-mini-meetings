"""Faces in the robot camera, found on this computer instead of the headset.

On the headset the detector ran at ~3 frames/s, too few to tell from mouth movement who is talking (it is
judged over 1.5 s). Here MediaPipe's FaceLandmarker gets ~54 frames/s at 960x540 (8 ms per frame, M-series
Mac, 2026-10-08), so the page gets the faces and mouth openness at --vision-hz and turns its own detector off.

The camera is read directly (macOS / Windows / Linux let a second app read it next to the daemon) and found
by name, not by index. Output per person, same as xr-client/pages/faces-worker.js: {cx, cy, top, w, h, mouth},
normalized 0..1 (origin top left), mouth = jawOpen 0..1, and lips = lip gap / face height (people.py uses that).
Time = wall clock of the capture.

Who a face is (2026-11-18): each followed face also gets "track" (a number that stays while the face is followed)
and "emb", an embedding of the face itself (FaceId: OpenCV's SFace on the face turned upright by its eyes, nose and
mouth corners), so people.py knows a person again after Reachy's head turned or they changed seats. Until then a
person was only a direction in the room, and one face became up to five people while the head moved. Both stay on
this computer: captions.py does not send them to the headset. Measured in scripts/eval_faces.py.
"""

from __future__ import annotations

import logging
import math
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

FACE_MODEL_URL = "https://huggingface.co/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx"
FACE_MODEL_PATH = Path.home() / ".cache" / "reachy-meetings" / "face_recognition_sface_2021dec.onnx"
# Where the two eyes, the nose tip and the two mouth corners belong in the 112x112 picture the model was trained on
# (left to right as seen in the image)
FACE_TEMPLATE = ((38.2946, 51.6963), (73.5318, 51.5014), (56.0252, 71.7366), (41.5493, 92.3655), (70.7299, 92.2041))
FACE_POINTS = (468, 473, 4, 61, 291)   # MediaPipe landmarks: iris centres, nose tip, mouth corners
EMB_EVERY_S = 0.4     # a followed face is looked at again this often
EMB_MIN_PX = 16       # eyes closer together than this (pixels): too small to say who it is (eval_faces.py: at 18 px
                      # still 3.4 % wrong, 5.8 % in dim light; at 1.5-2 m they are 30-40 px apart)
EMB_FRONTAL = 0.45    # eye distance / eye-to-mouth distance below this: turned too far away (frontal ~0.9)
EMB_MEAN_OF = 12      # a track's embedding is the mean of about this many looks
TRACK_OTHER = 0.0     # a look this unlike the track's face so far: the track slipped onto someone else


class FaceId:
    """An embedding of a face (unit vector, 128 numbers): the same person's faces are close (cosine), whoever
    else's are not. frame = RGB, pts = the five FACE_POINTS in pixels."""

    def __init__(self, path: Path | None = None) -> None:
        import cv2

        path = path or FACE_MODEL_PATH
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".part")
            urllib.request.urlretrieve(FACE_MODEL_URL, tmp)
            tmp.rename(path)
        self.net = cv2.FaceRecognizerSF.create(str(path), "")

    @staticmethod
    def order(pts):
        """Eyes and mouth corners left to right as in the image, as the template has them."""
        import numpy as np

        p = np.asarray(pts, np.float64)
        eyes, mouth = sorted(p[:2].tolist()), sorted(p[3:].tolist())
        return np.array([*eyes, p[2].tolist(), *mouth])

    @staticmethod
    def usable(pts) -> bool:
        import numpy as np

        p = FaceId.order(pts)
        eyes = np.linalg.norm(p[1] - p[0])
        down = np.linalg.norm(p[3:].mean(0) - p[:2].mean(0))
        return eyes >= EMB_MIN_PX and down > 0 and eyes / down >= EMB_FRONTAL

    def __call__(self, frame, pts):
        import cv2
        import numpy as np

        src, dst = self.order(pts), np.array(FACE_TEMPLATE)
        # least-squares similarity transform (rotation, scale, shift) from the face to the template
        a, b = src - src.mean(0), dst - dst.mean(0)
        u, sv, vt = np.linalg.svd(b.T @ a)
        d = np.sign(np.linalg.det(u @ vt))
        r = u @ np.diag([1.0, d]) @ vt
        scale = (sv * [1.0, d]).sum() / (a**2).sum()
        m = np.hstack([scale * r, (dst.mean(0) - scale * r @ src.mean(0))[:, None]])
        face = cv2.warpAffine(frame, m, (112, 112), flags=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
        e = self.net.feature(cv2.cvtColor(face, cv2.COLOR_RGB2BGR))[0]
        return e / np.linalg.norm(e)


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

        try:
            face_id = FaceId()
        except Exception as e:   # no download, an OpenCV without it: people are told apart by their place only
            log.warning("Vision: no face model (%s), people are known by where they sit only", e)
            face_id = None
        finder = FaceFinder(self._landmarker(), face_id)
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
            people = finder.step(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), t)
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


LIP_PAIRS = ((82, 87), (13, 14), (312, 317))   # MediaPipe landmarks: upper and lower inner lip, left/middle/right
FACE_TOP, FACE_CHIN = 10, 152


def lips(xs, ys, W: int, H: int) -> float | None:
    """How far the lips are apart, as a fraction of the face height (forehead to chin), from the landmarks.
    jawOpen (the blendshape) barely moved for someone talking with little jaw movement (2026-11-23 recording:
    0.006-0.011 while reading aloud, a listener's smile 0.03)."""
    if len(xs) <= max(FACE_CHIN, *(k for p in LIP_PAIRS for k in p)):
        return None

    def d(a, b):
        return math.hypot((xs[a] - xs[b]) * W, (ys[a] - ys[b]) * H)

    face = d(FACE_TOP, FACE_CHIN)
    return round(sum(d(a, b) for a, b in LIP_PAIRS) / len(LIP_PAIRS) / face, 4) if face > 0 else None


class FaceFinder:
    """Faces in a frame (RGB), as vision.py publishes them. Needs a landmarker in IMAGE mode."""

    def __init__(self, landmarker, face_id=None) -> None:
        import numpy as np

        self.landmarker = landmarker
        self.face_id = face_id         # FaceId or None: faces without "emb"
        self.brighten = np.clip(255 * (np.arange(256) / 255.0) ** GAMMA, 0, 255).astype(np.uint8)
        self.tracks: list[dict] = []   # {"face": the last answer, "missed": frames, "id", "emb", "n", "t"}
        self.k = 0
        self.next_id = 1

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
                          "w": round(fx1 - fx0, 4), "h": round(fy1 - fy0, 4), "mouth": round(jaw, 3),
                          "lips": lips(xs, ys, W, H),
                          "pts": [(xs[k] * W, ys[k] * H) for k in FACE_POINTS] if len(pts) > max(FACE_POINTS) else None})
        return faces

    def _look(self, tr: dict, frame, t: float) -> None:
        """Who the track's face is: a new look every EMB_EVERY_S, averaged."""
        import numpy as np

        pts = tr["face"].get("pts")
        if self.face_id is None or pts is None or t - tr["t"] < EMB_EVERY_S or not FaceId.usable(pts):
            return
        frame = self._lit(frame, pts)
        e = self.face_id(frame, pts)
        tr["t"] = t
        if tr["emb"] is not None and float(e @ tr["emb"]) < TRACK_OTHER:
            # not the face this track has followed so far (two people crossed): it is a new track from here on
            tr.update(id=self.next_id, emb=None, n=0)
            self.next_id += 1
        n = min(tr["n"], EMB_MEAN_OF - 1)
        m = e if tr["emb"] is None else (tr["emb"] * n + e) / (n + 1)
        tr["emb"], tr["n"] = m / np.linalg.norm(m), tr["n"] + 1

    def _lit(self, frame, pts):
        """The part of the frame around the face, brightened if it is dark (as for the detection); the points stay
        valid: the frame keeps its size, only that part is replaced in a copy."""
        import cv2
        import numpy as np

        p = np.asarray(pts)
        (x0, y0), (x1, y1) = p.min(0), p.max(0)
        pad = 1.5 * max(x1 - x0, y1 - y0)
        x0, y0 = max(0, int(x0 - pad)), max(0, int(y0 - pad))
        x1, y1 = min(frame.shape[1], int(x1 + pad)), min(frame.shape[0], int(y1 + pad))
        part = frame[y0:y1, x0:x1]
        if part.size == 0 or part[::2, ::2].mean() >= DARK:
            return frame
        out = frame.copy()
        out[y0:y1, x0:x1] = cv2.LUT(part, self.brighten)
        return out

    def _new_track(self, face: dict) -> dict:
        tr = {"face": face, "missed": 0, "id": self.next_id, "emb": None, "n": 0, "t": -1e9}
        self.next_id += 1
        return tr

    @staticmethod
    def _out(tr: dict) -> dict:
        return {**{k: v for k, v in tr["face"].items() if k != "pts"}, "track": tr["id"], "emb": tr["emb"]}

    @staticmethod
    def _same(a: dict, b: dict) -> bool:
        return abs(a["cx"] - b["cx"]) < 0.6 * max(a["w"], b["w"]) and abs(a["cy"] - b["cy"]) < 0.6 * max(a["h"], b["h"])

    def step(self, frame, t: float | None = None) -> list[dict]:
        """The faces in this frame (taken at wall clock t): {cx, cy, top, w, h, mouth, track, emb}."""
        H, W = frame.shape[:2]
        t = time.time() if t is None else t
        for tr in self.tracks:   # follow the known faces, each in a close crop
            f = tr["face"]
            side = CROP * max(f["w"] * W, f["h"] * H)
            found = self._detect(frame, f["cx"] - side / W / 2, f["cy"] - side / H / 2, side / W, side / H)
            found = [g for g in found if self._same(f, g)]
            if found:
                tr["face"], tr["missed"] = found[0], 0
            else:
                tr["missed"] += 1
        self.tracks = [tr for tr in self.tracks if tr["missed"] < LOST]
        # Two tracks can end up on the same face (a crop around one face finds its neighbour, or both follow one
        # face after the other left): keep the first. 2026-11-18: one face came as three people (3.8 faces per
        # frame, three people with the same mouth readings), so no mouth ever stood out and the votes split.
        kept: list[dict] = []
        for tr in self.tracks:
            if not any(self._same(k["face"], tr["face"]) for k in kept):
                kept.append(tr)
        self.tracks = kept
        tile = TILES[self.k % len(TILES)]   # and look for new ones in one piece of the picture
        self.k += 1
        for g in self._detect(frame, *tile):
            if not any(self._same(tr["face"], g) for tr in self.tracks):
                self.tracks.append(self._new_track(g))
        seen = [tr for tr in self.tracks if tr["missed"] == 0]
        for tr in seen:
            self._look(tr, frame, t)
        return [self._out(tr) for tr in seen]
