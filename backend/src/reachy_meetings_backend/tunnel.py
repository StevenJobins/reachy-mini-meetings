"""Wireless access for the headset: the page on GitHub Pages is https, so it can only reach this server over
wss with a valid certificate. A Cloudflare quick tunnel (no account) gives one, at a random address that
changes on every start; the address is posted to an ntfy.sh topic, where the page looks it up.

The address alone gives no access: tunneled clients must send an allowed Hugging Face sign-in first
(see CaptionServer). Keep TOPIC in sync with xr-client/pages/captions.js.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.request

log = logging.getLogger("reachy_captions.tunnel")

TOPIC = "reachy-meetings-xr-captions"
REPOST_S = 1800   # ntfy.sh keeps messages for 12 h; repost so the page always finds the current address
URL_RE = re.compile(rb"https://[a-z0-9-]+\.trycloudflare\.com")


def publish(url: str) -> None:
    req = urllib.request.Request(f"https://ntfy.sh/{TOPIC}", data=url.encode(), method="POST")
    urllib.request.urlopen(req, timeout=10).close()


def lookup() -> str | None:
    """Latest published address (for tests and scripts)."""
    with urllib.request.urlopen(f"https://ntfy.sh/{TOPIC}/json?poll=1&since=latest", timeout=10) as r:
        lines = r.read().decode().strip().splitlines()
    return json.loads(lines[-1]).get("message") if lines else None


async def run(port: int) -> None:
    """Keep a quick tunnel to ws://localhost:<port> open and its wss address published; restart if it dies."""
    while True:
        proc = await asyncio.create_subprocess_exec(
            "cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://localhost:{port}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        url = None
        repost = None
        try:
            async for line in proc.stderr:
                m = URL_RE.search(line)
                if m and url is None:
                    url = "wss://" + m.group().decode().removeprefix("https://")
                    log.info("Tunnel: %s", url)
                    repost = asyncio.create_task(_keep_published(url))
            await proc.wait()
        finally:
            if repost:
                repost.cancel()
            if proc.returncode is None:
                proc.terminate()
        log.warning("cloudflared exited (%s), restarting in 5 s", proc.returncode)
        await asyncio.sleep(5)


async def _keep_published(url: str) -> None:
    while True:
        try:
            await asyncio.to_thread(publish, url)
        except OSError as e:
            log.warning("Could not publish the tunnel address: %s", e)
            await asyncio.sleep(30)
            continue
        await asyncio.sleep(REPOST_S)
