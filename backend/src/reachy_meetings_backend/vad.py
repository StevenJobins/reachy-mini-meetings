"""Neural voice activity detection (Silero VAD v5, ONNX). Tells speech from motor noise, clicks, hum and
music far better than a loudness threshold, so Whisper is not fed noise (where it invents text like
"Vielen Dank." or "Untertitelung des ZDF"). ~2 MB model, ~0.1 ms per 32 ms frame on a laptop CPU.

The model is downloaded once into ~/.cache/reachy-meetings/.
"""

from __future__ import annotations

import logging
import urllib.request
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

URL = "https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx"
CACHE = Path.home() / ".cache" / "reachy-meetings" / "silero_vad.onnx"
FRAME = 512    # samples at 16 kHz (32 ms), fixed by the model
CONTEXT = 64   # v5 expects the last 64 samples of the previous frame in front of each frame


class SileroVad:
    def __init__(self, path: Path = CACHE) -> None:
        import onnxruntime as ort

        if not path.exists():
            log.info("Downloading Silero VAD to %s", path)
            path.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(URL, path)
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        self.session = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
        self.sr = np.array(16000, np.int64)
        self.reset()

    def reset(self) -> None:
        self.state = np.zeros((2, 1, 128), np.float32)
        self.context = np.zeros(CONTEXT, np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        """Speech probability 0..1 of one 512-sample frame."""
        x = np.concatenate([self.context, frame.astype(np.float32)])[None, :]
        out, self.state = self.session.run(None, {"input": x, "state": self.state, "sr": self.sr})
        self.context = x[0, -CONTEXT:]
        return float(out[0][0])
