"""Translation of final utterances. Both translators get the last few utterances as context,
which helps with names, pronouns and half-sentences that Whisper cut in the middle.

DeepLTranslator   default. Free Developer plan (our key: 500k characters, ~10 meeting hours).
                  Key from deepl.com/your-account/keys in DEEPL_AUTH_KEY.
ClaudeTranslator  optional (`pip install -e ".[claude]"`, ANTHROPIC_API_KEY), paid per token.
                  Also fixes speech-recognition errors from context.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import deque

log = logging.getLogger(__name__)


class DeepLTranslator:
    def __init__(self, target: str, context: int = 3) -> None:
        import deepl

        key = os.environ.get("DEEPL_AUTH_KEY")
        if not key:
            raise RuntimeError("DEEPL_AUTH_KEY is not set (key: deepl.com/your-account/keys)")
        self._deepl = deepl
        self.client = deepl.Translator(key)  # picks the right endpoint from the key
        # DeepL wants a variant for some targets
        self.target = {"EN": "EN-US", "PT": "PT-PT"}.get(target.upper(), target.upper())
        self.history: deque[str] = deque(maxlen=context)

    async def __call__(self, text: str) -> str | None:
        context = " ".join(self.history) or None
        self.history.append(text)
        try:
            result = await asyncio.to_thread(self.client.translate_text, text,
                                             target_lang=self.target, context=context)
        except self._deepl.QuotaExceededException:
            log.warning("DeepL: character quota used up")
            return None
        except self._deepl.DeepLException as e:
            log.warning("translation failed: %s", e)
            return None
        return result.text.strip() or None


CLAUDE_SYSTEM = (
    "You translate live meeting transcripts for a remote participant who reads them as "
    "speech bubbles. Translate the utterance into {target}. Keep it short and natural, keep "
    "names and technical terms. The transcript comes from speech recognition and may contain "
    "errors; fix obvious ones. Reply with the translation only. If the utterance is already in "
    "{target}, reply with it unchanged."
)


class ClaudeTranslator:
    def __init__(self, target: str, model: str = "claude-opus-5-5", context: int = 3) -> None:
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.AsyncAnthropic()
        self.model = model
        self.system = CLAUDE_SYSTEM.format(target=f"the language with ISO 639-1 code '{target}'")
        self.history: deque[str] = deque(maxlen=context)

    async def __call__(self, text: str) -> str | None:
        prompt = ""
        if self.history:
            prompt += "Earlier in the meeting:\n" + "\n".join(self.history) + "\n\n"
        prompt += f"Utterance to translate:\n{text}"
        self.history.append(text)
        try:
            resp = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=1024,
                system=self.system,
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=[{"role": "user", "content": prompt}],
            )
        except self._anthropic.APIStatusError as e:
            log.warning("translation failed (%s): %s", e.status_code, e.message)
            return None
        except self._anthropic.APIConnectionError:
            log.warning("translation failed: no connection to the Claude API")
            return None
        if resp.stop_reason == "refusal":
            return None
        return "".join(b.text for b in resp.content if b.type == "text").strip() or None
