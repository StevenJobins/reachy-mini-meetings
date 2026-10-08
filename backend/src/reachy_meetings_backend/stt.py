"""Speech-to-text. Not thread-safe: call from one worker thread only.

Each Transcriber keeps its own model in memory. mlx-whisper itself caches only ONE model (ModelHolder), so
alternating the partial model (small) and the final model (large-v3-turbo) reloaded the big one from disk
before every final: +0.4-0.6 s per final (measured 2026-10-08, M-series Mac).

Engines:
  mlx             Apple Silicon GPU (mlx-whisper). M1 Pro, 4.5 s German utterance:
                  large-v3-turbo ~1.5 s, small ~0.35 s (+ ~1.3 s if the language is auto-detected)
  faster-whisper  everything else: NVIDIA GPU (CUDA) if present, else CPU
                  (CPU on the same Mac: small ~3 s for that clip -> no partials, small model)
"""

from __future__ import annotations

import logging
import time

import numpy as np

log = logging.getLogger(__name__)

AUTO = "auto"   # language argument: detect it, even if the Transcriber has a fixed language (--lang)

# Whisper invents text on noise and silence. Drop segments it is unsure about, and the classic phrases
# it learned from subtitled TV (German: "Untertitelung des ZDF", "Vielen Dank."; English: "Thank you.").
NO_SPEECH = 0.6
MIN_LOGPROB = -1.0
MAX_COMPRESSION = 2.4   # "1,0, 1,0, 1,0, ..." style repetition
ALWAYS_FAKE = ("untertitel", "amara.org", "zdf", "swr ", "copyright", "thanks for watching",
               "fürs zuschauen", "für's zuschauen", "abonnier")
FAKE_IF_UNSURE = {"vielen dank", "danke", "danke schön", "thank you", "thanks", "pff", "tschüss", "bis bald",
                  "ja", "okay", "you"}


def plausible(text: str, no_speech: float, logprob: float, compression: float) -> bool:
    t = text.lower().strip().strip(".!?,… ")
    # like Whisper's own rule: silence only if BOTH no-speech is likely and the text is unsure
    silent = no_speech >= NO_SPEECH and logprob < MIN_LOGPROB
    if not t or silent or logprob < 2 * MIN_LOGPROB or compression > MAX_COMPRESSION:
        return False
    if any(p in t for p in ALWAYS_FAKE):
        return False
    return not (t in FAKE_IF_UNSURE and (no_speech > 0.2 or logprob < -0.5))


def has_cuda() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except ImportError:
        return False


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
            import mlx.core as mx
            from mlx_whisper.load_models import load_model
            from mlx_whisper.transcribe import ModelHolder

            self._holder = ModelHolder
            self._model = load_model(self.repo, dtype=mx.float16)
            self(np.zeros(16000, np.float32))  # download + load now, not on the first utterance
        else:
            from faster_whisper import WhisperModel

            cuda = has_cuda()
            self._fw = WhisperModel(model, device="cuda" if cuda else "cpu",
                                    compute_type="float16" if cuda else "int8", cpu_threads=4)
        log.info("Whisper %r (%s) ready in %.1fs", model, self.engine, time.time() - t0)

    def __call__(self, audio: np.ndarray, language: str | None = None) -> tuple[str, str]:
        """Returns (text, language). `language` overrides the fixed one (e.g. detected by the partials);
        AUTO detects it in any case."""
        language = None if language == AUTO else language or self.language
        if self.engine == "mlx":
            self._holder.model, self._holder.model_path = self._model, self.repo   # no reload (see top)
            r = self._mlx.transcribe(audio, path_or_hf_repo=self.repo, language=language,
                                     condition_on_previous_text=False, verbose=None)
            parts = [s["text"] for s in r["segments"]
                     if plausible(s["text"], s["no_speech_prob"], s["avg_logprob"], s["compression_ratio"])]
            lang = r["language"]
        else:
            segments, info = self._fw.transcribe(audio, language=language, beam_size=1,
                                                  condition_on_previous_text=False)
            parts = [s.text for s in segments
                     if plausible(s.text, s.no_speech_prob, s.avg_logprob, s.compression_ratio)]
            lang = info.language
        return " ".join(p.strip() for p in parts).strip(), lang

    def detect_language(self, audio: np.ndarray) -> str:
        """Spoken language only (one encoder pass, no decoding): ~0.1 s with `small` on the Mac GPU."""
        if self.engine == "mlx":
            import mlx.core as mx
            from mlx_whisper.audio import N_FRAMES, log_mel_spectrogram, pad_or_trim

            mel = pad_or_trim(log_mel_spectrogram(audio, n_mels=self._model.dims.n_mels), N_FRAMES, axis=-2)
            _, probs = self._model.detect_language(mel.astype(mx.float16))
            return max(probs, key=probs.get)
        lang, _, _ = self._fw.detect_language(audio)
        return lang
