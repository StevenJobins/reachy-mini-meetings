"""Translation of final utterances with Claude (any target language).

The last few utterances go along as context, which helps with names, pronouns and
half-sentences that Whisper cut in the middle.
"""

from __future__ import annotations

import logging
from collections import deque

import anthropic

log = logging.getLogger(__name__)

SYSTEM = (
    "You translate live meeting transcripts for a remote participant who reads them as "
    "speech bubbles. Translate the utterance into {target}. Keep it short and natural, keep "
    "names and technical terms. The transcript comes from speech recognition and may contain "
    "errors; fix obvious ones. Reply with the translation only. If the utterance is already in "
    "{target}, reply with it unchanged."
)


class ClaudeTranslator:
    def __init__(self, target: str, model: str = "claude-opus-5-5", context: int = 3) -> None:
        self.client = anthropic.AsyncAnthropic()
        self.model = model
        self.system = SYSTEM.format(target=target)
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
        except anthropic.APIStatusError as e:
            log.warning("translation failed (%s): %s", e.status_code, e.message)
            return None
        except anthropic.APIConnectionError:
            log.warning("translation failed: no connection to the Claude API")
            return None
        if resp.stop_reason == "refusal":
            return None
        return "".join(b.text for b in resp.content if b.type == "text").strip() or None
