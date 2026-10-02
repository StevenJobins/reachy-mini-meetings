"""Live meeting notes: summary bullets + action items, with Google Gemini (free tier, gemini-flash-lite-latest).

Every `every_s` seconds, if new final utterances arrived, the transcript goes to Gemini's
OpenAI-compatible endpoint and comes back as JSON. Key from aistudio.google.com in GEMINI_API_KEY.
Note: on the free tier Google may use the content to improve its products, so it suits the course
project but not confidential meetings.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
MAX_LINES = 400  # newest utterances sent per request

SYSTEM = (
    "You write live notes for a meeting from its speech-recognition transcript, for a remote "
    "participant. Write in the language with ISO 639-1 code '{target}'. Reply with JSON only: "
    '{{"summary": ["short bullet", ...], "actions": [{{"who": "name or empty", "what": "task"}}, ...]}}. '
    "At most 6 summary bullets covering the whole meeting so far, newest topics last. Actions only "
    "for concrete tasks someone agreed to do. The transcript may contain recognition errors."
)


def parse_summary(text: str) -> dict | None:
    """Model reply -> {"summary": [str], "actions": [{"who", "what"}]}, None if unusable."""
    m = re.search(r"\{.*\}", text, re.DOTALL)  # tolerate ```json fences and chatter around the object
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    summary = [str(s).strip() for s in data.get("summary") or [] if str(s).strip()]
    actions = []
    for a in data.get("actions") or []:
        if isinstance(a, dict) and str(a.get("what", "")).strip():
            actions.append({"who": str(a.get("who") or "").strip(), "what": str(a["what"]).strip()})
        elif isinstance(a, str) and a.strip():
            actions.append({"who": "", "what": a.strip()})
    if not summary and not actions:
        return None
    return {"summary": summary, "actions": actions}


class Summarizer:
    def __init__(self, target: str, model: str, every_s: float = 60.0) -> None:
        self.key = os.environ.get("GEMINI_API_KEY")
        if not self.key:
            raise RuntimeError("GEMINI_API_KEY is not set (free key: aistudio.google.com/apikey)")
        self.system = SYSTEM.format(target=target)
        self.model = model
        self.every_s = every_s
        self.lines: list[str] = []
        self.new = 0
        self.latest: dict | None = None

    def add(self, caption: dict) -> None:
        if not caption["text"]:
            return
        line = f"[{time.strftime('%H:%M', time.localtime(caption['t_start']))}] {caption['text']}"
        if caption.get("translation"):
            line += f"  ({caption['translation']})"
        self.lines.append(line)
        self.new += 1

    def _request(self, transcript: str) -> str:
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "system", "content": self.system},
                         {"role": "user", "content": transcript}],
        }).encode()
        req = urllib.request.Request(URL, body, {"Content-Type": "application/json",
                                                 "Authorization": f"Bearer {self.key}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]

    async def run(self, publish) -> None:
        while True:
            await asyncio.sleep(self.every_s)
            if not self.new:
                continue
            self.new = 0
            transcript = "\n".join(self.lines[-MAX_LINES:])
            try:
                reply = await asyncio.to_thread(self._request, transcript)
            except urllib.error.HTTPError as e:
                log.warning("summary failed (%s): %s", e.code, e.read()[:300].decode(errors="replace"))
                continue
            except (OSError, KeyError, ValueError) as e:
                log.warning("summary failed: %s", e)
                continue
            notes = parse_summary(reply)
            if notes is None:
                log.warning("summary: unusable reply %r", reply[:200])
                continue
            self.latest = {**notes, "t": round(time.time(), 3)}
            log.info("summary: %d bullets, %d actions", len(notes["summary"]), len(notes["actions"]))
            publish(self.latest)
