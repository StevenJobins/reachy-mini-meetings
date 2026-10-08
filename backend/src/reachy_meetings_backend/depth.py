"""Metric depth for the room scan (WP2 extension): Depth Anything V2, metric indoor model, on the laptop GPU.

The page scans the room once after wake-up (and refreshes it while Reachy looks around) and sends each frame
here as JPEG; this returns the metric depth (metres, along the optical axis) sampled at the page's mesh grid,
so the headset can turn the room panorama into 3D geometry (parallax when the user moves the head).

Optional: pip install -e ".[depth]" (torch, transformers, pillow). Without it the page keeps the flat
panorama (rotation-only reprojection). Device: Apple GPU (mps), CUDA, else CPU.
"""

from __future__ import annotations

import io
import logging
import threading

import numpy as np

log = logging.getLogger(__name__)

MODEL = "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf"


class DepthEstimator:
    def __init__(self, model: str = MODEL) -> None:
        self.model_id = model
        self._pipe = None
        self._lock = threading.Lock()
        self.error: str | None = None

    def _load(self):
        if self._pipe is None and self.error is None:
            try:
                import torch
                from transformers import pipeline
                device = ("mps" if torch.backends.mps.is_available()
                          else "cuda" if torch.cuda.is_available() else "cpu")
                self._pipe = pipeline("depth-estimation", model=self.model_id, device=device)
                log.info("Depth model %s on %s", self.model_id, device)
            except Exception as e:   # missing extra, no network for the first download, ...
                self.error = f"{type(e).__name__}: {e}"
                log.warning("Depth unavailable (%s). pip install -e '.[depth]'", self.error)
        return self._pipe

    def estimate(self, jpeg: bytes, w: int, h: int) -> list[float]:
        """Depth in metres at a w x h grid of image points (u = i/(w-1), v = j/(h-1)), row-major.
        Blocking (~0.1-0.5 s on a laptop GPU): call it in a thread."""
        from PIL import Image
        with self._lock:
            pipe = self._load()
            if pipe is None:
                raise RuntimeError(self.error or "depth model not loaded")
            img = Image.open(io.BytesIO(jpeg)).convert("RGB")
            depth = pipe(img)["predicted_depth"]
        d = np.asarray(depth.squeeze().float().cpu().numpy() if hasattr(depth, "cpu") else depth, np.float32)
        # bilinear sampling at the grid vertices (the corners of the image are vertices too)
        H, W = d.shape
        ys, xs = np.linspace(0, H - 1, h), np.linspace(0, W - 1, w)
        y0 = np.floor(ys).astype(int); x0 = np.floor(xs).astype(int)
        y1 = np.minimum(y0 + 1, H - 1); x1 = np.minimum(x0 + 1, W - 1)
        fy = (ys - y0)[:, None]; fx = (xs - x0)[None, :]
        g = (d[np.ix_(y0, x0)] * (1 - fy) * (1 - fx) + d[np.ix_(y0, x1)] * (1 - fy) * fx
             + d[np.ix_(y1, x0)] * fy * (1 - fx) + d[np.ix_(y1, x1)] * fy * fx)
        return [round(float(v), 3) for v in g.ravel()]
