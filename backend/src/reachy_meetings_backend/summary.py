"""Live meeting notes: summary bullets, action items and next steps, with Google Gemini (free tier, gemini-flash-lite-latest).

Every `every_s` seconds, if new final utterances arrived, the transcript goes to Gemini's
OpenAI-compatible endpoint and comes back as JSON. Key from aistudio.google.com in GEMINI_API_KEY.

One meeting at a time: after `gap_s` without speech (default 20 min) the next utterance starts a new meeting,
and a client can start one with {"type": "new_meeting"}. Then the transcript is dropped and empty notes are
published, so the headset clears its card; a summary still being written for the old meeting is thrown away.
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
    '{{"summary": ["short bullet", ...], "actions": [{{"who": "name or empty", "what": "task", '
    '"when": "deadline or empty"}}, ...], "next_steps": ["short bullet", ...]}}. '
    "At most 5 summary bullets covering the whole meeting so far, newest topics last. Actions only "
    "for concrete tasks someone agreed to do. Next steps: at most 4 open questions, decisions still to "
    "take or topics for the next meeting, not repeating the actions. The transcript may contain "
    "recognition errors."
)


def parse_summary(text: str) -> dict | None:
    """Model reply -> {"summary": [str], "actions": [{"who", "what", "when"}], "next_steps": [str]}, None if unusable."""
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
            actions.append({"who": str(a.get("who") or "").strip(), "what": str(a["what"]).strip(),
                            "when": str(a.get("when") or "").strip()})
        elif isinstance(a, str) and a.strip():
            actions.append({"who": "", "what": a.strip(), "when": ""})
    next_steps = [str(s).strip() for s in data.get("next_steps") or [] if str(s).strip()]
    if not summary and not actions and not next_steps:
        return None
    return {"summary": summary, "actions": actions, "next_steps": next_steps}


class Summarizer:
    def __init__(self, target: str, model: str, every_s: float = 60.0, gap_s: float = 20 * 60) -> None:
        self.key = os.environ.get("GEMINI_API_KEY")
        if not self.key:
            raise RuntimeError("GEMINI_API_KEY is not set (free key: aistudio.google.com/apikey)")
        self.system = SYSTEM.format(target=target)
        self.model = model
        self.every_s = every_s
        self.gap_s = gap_s
        self.lines: list[str] = []
        self.now = asyncio.Event()   # write the notes now (language changed), not at the next interval
        self.new = 0
        self.latest: dict | None = None
        self.meeting = 0                    # counts up with every new meeting
        self.last_t: float | None = None    # end of the last utterance
        self.publish = None                 # set by run()

    def reset(self, reason: str) -> None:
        """New meeting: forget the transcript and the notes, tell the clients (empty notes)."""
        self.lines.clear()
        self.new = 0
        self.last_t = None
        self.meeting += 1
        self.latest = {"summary": [], "actions": [], "next_steps": [], "t": round(time.time(), 3), "new_meeting": True}
        log.info("summary: new meeting (%s)", reason)
        if self.publish:
            self.publish(self.latest)

    def set_target(self, target: str) -> None:
        """Language of the notes (the bubble language, switchable from the page). The notes there are written
        again in it at once, not only after the next sentences."""
        self.system = SYSTEM.format(target=target)
        if self.lines:
            self.new += 1
            self.now.set()

    def add(self, caption: dict) -> None:
        if not caption["text"]:
            return
        if self.lines and self.last_t is not None and caption["t_start"] - self.last_t > self.gap_s:
            self.reset(f"{(caption['t_start'] - self.last_t) / 60:.0f} min without speech")
        self.last_t = caption.get("t_end") or caption["t_start"]
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
        self.publish = publish
        while True:
            try:
                await asyncio.wait_for(self.now.wait(), self.every_s)
            except asyncio.TimeoutError:
                pass
            self.now.clear()
            if not self.new:
                continue
            self.new = 0
            meeting = self.meeting
            transcript = "\n".join(self.lines[-MAX_LINES:])
            try:
                reply = await asyncio.to_thread(self._request, transcript)
            except urllib.error.HTTPError as e:
                log.warning("summary failed (%s): %s", e.code, e.read()[:300].decode(errors="replace"))
                continue
            except (OSError, KeyError, ValueError) as e:
                log.warning("summary failed: %s", e)
                continue
            if meeting != self.meeting:   # a new meeting began while Gemini was writing
                continue
            notes = parse_summary(reply)
            if notes is None:
                log.warning("summary: unusable reply %r", reply[:200])
                continue
            self.latest = {**notes, "t": round(time.time(), 3)}
            log.info("summary: %d bullets, %d actions, %d next steps",
                     len(notes["summary"]), len(notes["actions"]), len(notes["next_steps"]))
            publish(self.latest)
