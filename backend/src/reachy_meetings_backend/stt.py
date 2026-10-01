"""Speech-to-text. Not thread-safe: call from one worker thread only.

Engines:
  mlx             Apple Silicon GPU (mlx-whisper). M1 Pro, 4.5 s German utterance:
                  large-v3-turbo ~1.5 s, small ~0.35 s (+ ~1.3 s if the language is auto-detected)
  faster-whisper  CPU fallback for everything else (small ~3 s on the same clip, too slow for partials)
"""

from __future__ import annotations

import logging
import time

import numpy as np

log = logging.getLogger(__name__)

NO_SPEECH = 0.6  # Whisper invents text on noise ("Thank you.", "Untertitel von ..."), drop those


def default_engine() -> str:
    try:
        import mlx_whisper  # noqa: F401
        return "mlx"
    except ImportError:
        return "faster-whisper"


class Transcriber:
    def __init__(self, model: str, language: str | None = None, engine: str | None = None) -> None:
        self.engine = engine or default_engine()
        self.language = language
        t0 = time.time()
        if self.engine == "mlx":
            import mlx_whisper

            self._mlx = mlx_whisper
            from huggingface_hub import snapshot_download

            if "/" in model:
                repo = model
            elif model == "large-v3-turbo":
                repo = "mlx-community/whisper-large-v3-turbo"
            else:
                repo = f"mlx-community/whisper-{model}-mlx"
            # local path, otherwise mlx-whisper asks the HF hub on every call
            self.repo = snapshot_download(repo)
            self(np.zeros(16000, np.float32))  # download + load now, not on the first utterance
        else:
            from faster_whisper import WhisperModel

            self._fw = WhisperModel(model, compute_type="int8", cpu_threads=4)
        log.info("Whisper %r (%s) ready in %.1fs", model, self.engine, time.time() - t0)

    def __call__(self, audio: np.ndarray) -> tuple[str, str]:
        """Returns (text, language)."""
        if self.engine == "mlx":
            r = self._mlx.transcribe(audio, path_or_hf_repo=self.repo, language=self.language,
                                     condition_on_previous_text=False, verbose=None)
            parts = [s["text"] for s in r["segments"] if s["no_speech_prob"] < NO_SPEECH]
            lang = r["language"]
        else:
            segments, info = self._fw.transcribe(audio, language=self.language, beam_size=1,
                                                  condition_on_previous_text=False)
            parts = [s.text for s in segments if s.no_speech_prob < NO_SPEECH]
            lang = info.language
        return " ".join(p.strip() for p in parts).strip(), lang
