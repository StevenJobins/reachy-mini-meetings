"""Wireless access for the headset: the page on GitHub Pages is https, so it can only reach this server over
wss with a valid certificate. A Cloudflare quick tunnel (no account) gives one, at a random address that
changes on every start; the address is posted to an ntfy.sh topic, where the page looks it up.

The address alone gives no access: tunneled clients must send an allowed Hugging Face sign-in first
(see CaptionServer). Keep TOPIC in sync with xr-client/pages/captions.js.

The topic is public, so anyone could post their own address there and collect the sign-in tokens the page
sends (code review 2026-10-08). So the address is SIGNED: the backend keeps an ECDSA P-256 key in
~/.config/reachy-meetings/tunnel_key.pem (created on first start, never in the repo) and posts
{"url", "ts", "sig", "key"}; the page only connects to addresses signed by a key it trusts (its list in
captions.js, plus keys added in Settings for other laptops). The public key is logged on start.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("reachy_captions.tunnel")

TOPIC = "reachy-meetings-xr-captions"
REPOST_S = 1800   # ntfy.sh keeps messages for 12 h; repost so the page always finds the current address
# "api.trycloudflare.com" shows up in cloudflared's error lines when a tunnel request fails: not an address
URL_RE = re.compile(rb"https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com")
KEY_PATH = Path.home() / ".config" / "reachy-meetings" / "tunnel_key.pem"


def signing_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    if KEY_PATH.exists():
        return serialization.load_pem_private_key(KEY_PATH.read_bytes(), None)
    key = ec.generate_private_key(ec.SECP256R1())
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEY_PATH.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    KEY_PATH.chmod(0o600)
    return key


def public_key_b64(key) -> str:
    """SPKI DER, base64: the form the page imports with crypto.subtle.importKey("spki", ...)."""
    from cryptography.hazmat.primitives import serialization

    return base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode()


def signed_message(url: str, key, ts: int | None = None) -> str:
    """{"url", "ts", "sig", "key"}; sig = ECDSA P-256 / SHA-256 over f"{url}|{ts}", raw r||s (WebCrypto's form)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    ts = int(time.time()) if ts is None else ts
    r, s = decode_dss_signature(key.sign(f"{url}|{ts}".encode(), ec.ECDSA(hashes.SHA256())))
    sig = base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode()
    return json.dumps({"url": url, "ts": ts, "sig": sig, "key": public_key_b64(key)})


def publish(message: str) -> None:
    req = urllib.request.Request(f"https://ntfy.sh/{TOPIC}", data=message.encode(), method="POST")
    urllib.request.urlopen(req, timeout=10).close()


def lookup() -> str | None:
    """Latest published address (for tests and scripts; not verified)."""
    with urllib.request.urlopen(f"https://ntfy.sh/{TOPIC}/json?poll=1&since=latest", timeout=10) as r:
        lines = r.read().decode().strip().splitlines()
    if not lines:
        return None
    msg = json.loads(lines[-1]).get("message", "")
    try:
        return json.loads(msg)["url"]
    except (ValueError, KeyError, TypeError):
        return msg


async def run(port: int) -> None:
    """Keep a quick tunnel to ws://localhost:<port> open and its wss address published; restart if it dies."""
    key = signing_key()
    log.info("Tunnel signing key (trust it on the page, Settings, for another laptop): %s", public_key_b64(key))
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
                    repost = asyncio.create_task(_keep_published(url, key))
            await proc.wait()
        finally:
            if repost:
                repost.cancel()
            if proc.returncode is None:
                proc.terminate()
        log.warning("cloudflared exited (%s), restarting in 5 s", proc.returncode)
        await asyncio.sleep(5)


async def _keep_published(url: str, key) -> None:
    while True:
        try:
            await asyncio.to_thread(publish, signed_message(url, key))
        except OSError as e:
            log.warning("Could not publish the tunnel address: %s", e)
            await asyncio.sleep(30)
            continue
        await asyncio.sleep(REPOST_S)
