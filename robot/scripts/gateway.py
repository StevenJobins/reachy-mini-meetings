"""The one door from the headset to this laptop: port 8790, open only with a headset's token.

Everything the Unity app needs here goes through it, so the parts themselves can stay on 127.0.0.1:

    /daemon/...    -> 127.0.0.1:8000   Reachy daemon: set_target, state, wake-up (has no password of its own)
    /signalling    -> 127.0.0.1:8443   video signalling (the daemon's webrtcsink)
    /backend       -> 127.0.0.1:8766   captions, translation, room sound
    /time          -> 127.0.0.1:8444   clock for the latency tests

Every request must carry `Authorization: Bearer <token>` of a known headset, else it gets 401 and nothing is
forwarded. The video itself (WebRTC, UDP) still goes straight between headset and laptop. Standard library only,
any python3:

    python3 robot/scripts/gateway.py                    # run (start_meeting.sh does)
    python3 robot/scripts/gateway.py code               # the pairing code of the running gateway
    python3 robot/scripts/gateway.py list
    python3 robot/scripts/gateway.py remove "Kevin's XR"
    python3 robot/scripts/gateway.py add "Kevin's XR"   # without the code: prints a one-time adb line instead

A new headset gets its token by itself (Pairing.cs in the app), with the six-digit code this laptop shows:
  1. it asks the Wi-Fi who is a gateway: a UDP broadcast "reachy-meetings?" to this port, answered with
     "reachy-meetings 1 <port> <laptop id> <laptop name>";
  2. it sends the code: POST /pair {"code", "name"} (the one request that needs no token), answered with
     {"token", "id", "name"}.
The code is good for one headset and for 10 minutes, then there is a new one; after 5 wrong codes there is a new one
too, and a minute's pause. The same search lets a headset find this laptop again when its address has changed.

From another network (`--remote`, off by default): a Cloudflare quick tunnel (`cloudflared`, no account) gives this
laptop a public https address, new on every start, that leads to a second door on 127.0.0.1:8791. That door is the
same as the first except that it does not pair: a headset pairs on the same Wi-Fi and may then come from anywhere.
So that a paired headset finds the address, it is posted on ntfy.sh as a notice only that headset can find and
check: the topic and a signature are both made from its token (topic(), notice(); RemoteAddress.cs in the app).

    python3 robot/scripts/gateway.py --remote           # also reachable from other networks
    python3 robot/scripts/gateway.py remote             # is it, right now?

Headsets are kept in ~/.config/reachy-meetings/headsets.json (name -> token, readable by this user only); the
running gateway picks up changes by itself.
"""

import argparse
import asyncio
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PORT = 8790   # not 8770: macOS itself listens there (sharingd)
ROUTES = {"daemon": 8000, "signalling": 8443, "backend": 8766, "time": 8444}
CONFIG = Path.home() / ".config" / "reachy-meetings"
HEADSETS = CONFIG / "headsets.json"
PAIRING_CODE = CONFIG / "pairing_code.json"   # the running gateway's current code, for the status view
LAPTOP_ID = CONFIG / "laptop_id"
HEAD_TIMEOUT_S = 5
CODE_S, WRONG_CODES, PAUSE_S = 600, 5, 60
DISCOVER = b"reachy-meetings?"
PUBLIC_PORT = 8791            # the door the tunnel leads to: 127.0.0.1 only, no pairing
REMOTE_STATE = CONFIG / "remote.json"   # the running gateway's public address, for the status view
BOARD = "https://ntfy.sh"     # where the public address is posted; keeps a notice for 12 h
REPOST_S, NOTICE_MAX_AGE_S = 1800, 12 * 3600
TUNNEL_URL = re.compile(rb"https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com")   # api.…: in cloudflared's error lines
APP = "ch.ethz.reachymeetings/com.unity3d.player.UnityPlayerGameActivity"


# ---------------------------------------------------------------- headsets
def load_headsets(path: Path = HEADSETS) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str) and v} if isinstance(data, dict) else {}


def save_headsets(headsets: dict, path: Path = HEADSETS) -> None:
    write_private(path, json.dumps(headsets, indent=1))


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def add_headset(name: str, path: Path = HEADSETS) -> str:
    """A new token for this name; a headset of the same name is replaced (its old token stops working)."""
    headsets = load_headsets(path)
    headsets[name] = secrets.token_urlsafe(24)
    save_headsets(headsets, path)
    return headsets[name]


def headset_of(token, headsets: dict):
    """Name of the headset with this token, or None. Compares all of them, in constant time each."""
    found = None
    for name, known in headsets.items():
        if isinstance(token, str) and hmac.compare_digest(token.encode(), known.encode()):
            found = name
    return found


# ---------------------------------------------------------------- pairing
class Pairing:
    """The six-digit code a new headset needs. One code at a time: it ends when a headset used it, after CODE_S,
    or after WRONG_CODES wrong tries (then nothing is accepted for PAUSE_S: guessing gets 5 tries a minute against
    a million codes). The current code is also written to `code_path` for the status view."""

    def __init__(self, headsets_path: Path = HEADSETS, code_path=PAIRING_CODE, now=time.time) -> None:
        self.headsets_path, self.code_path, self.now = headsets_path, code_path, now
        self.code, self.until, self.wrong, self.paused_until = "", 0.0, 0, 0.0

    def _new(self) -> None:
        self.code, self.until, self.wrong = f"{secrets.randbelow(10**6):06d}", self.now() + CODE_S, 0
        if self.code_path is not None:
            write_private(self.code_path, json.dumps({"code": self.code, "until": self.until}))

    def current(self) -> str:
        if not self.code or self.now() >= self.until:
            self._new()
        return self.code

    def pair(self, code, name):
        """(status, answer): the token for the right code, else why not."""
        if self.now() < self.paused_until:
            return "429 Too Many Requests", {"error": "too many wrong codes, wait a minute"}
        good = isinstance(code, str) and hmac.compare_digest(code.encode(), self.current().encode())
        if not good:
            self.wrong += 1
            if self.wrong >= WRONG_CODES:
                self.paused_until = self.now() + PAUSE_S
                self._new()
            return "403 Forbidden", {"error": "wrong code"}
        token = add_headset(headset_name(name), self.headsets_path)
        self._new()
        return "200 OK", {"token": token}


def headset_name(name) -> str:
    """What the headset calls itself, made safe for the list and the log."""
    name = "".join(c for c in name if c.isprintable()).strip()[:40] if isinstance(name, str) else ""
    return name or "headset"


def laptop_id(path: Path = LAPTOP_ID) -> str:
    """Stays the same for this laptop, so a headset knows it again at a new address."""
    try:
        known = path.read_text().strip()
    except OSError:
        known = ""
    if not known:
        known = secrets.token_hex(8)
        write_private(path, known)
    return known


def laptop_name() -> str:
    try:
        name = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True).stdout.strip()
    except OSError:
        name = ""
    if not name:
        import socket
        name = socket.gethostname()
    return headset_name(name)


def discovery_answer(port: int, ident: str, name: str) -> bytes:
    return f"reachy-meetings 1 {port} {ident} {name}".encode()


class Discovery(asyncio.DatagramProtocol):
    """Answers a headset's "who is a gateway?" on the Wi-Fi; says nothing to anything else."""

    def __init__(self, answer: bytes) -> None:
        self.answer = answer

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        if data.strip() == DISCOVER:
            self.transport.sendto(self.answer, addr)


# ---------------------------------------------------------------- from another network
def _mac(token: str, text: str) -> str:
    return hmac.new(token.encode(), text.encode(), "sha256").hexdigest()


def topic(token: str) -> str:
    """Where on the board this headset's notice is: only who has the token can work it out."""
    return "rm-" + _mac(token, "reachy-meetings address")[:40]


def notice(url: str, token: str, ts: int) -> str:
    """"<url> <unix time> <signature>": the headset takes an address only with its own token's signature, so
    nobody else can send it to a server of theirs (it would hand that server its token)."""
    return f"{url} {ts} {_mac(token, f'{url} {ts}')}"


def read_notice(text, token: str, now=time.time):
    """The address in a notice, or None: wrong signature, not wss, or older than the board keeps notices."""
    parts = text.split(" ") if isinstance(text, str) else []
    if len(parts) != 3 or not parts[0].startswith("wss://") or not parts[1].isdigit():
        return None
    if not hmac.compare_digest(parts[2], _mac(token, f"{parts[0]} {parts[1]}")):
        return None
    return parts[0] if now() - int(parts[1]) < NOTICE_MAX_AGE_S else None


def post_notice(board_topic: str, text: str) -> None:
    req = urllib.request.Request(f"{BOARD}/{board_topic}", data=text.encode(), method="POST")
    urllib.request.urlopen(req, timeout=10).close()


class Remote:
    """Keeps the tunnel open and its address posted for every known headset; starts the tunnel again if it dies."""

    def __init__(self, port: int = PUBLIC_PORT, headsets_path: Path = HEADSETS, state_path=REMOTE_STATE, log=print,
                 command=None, post=post_notice, now=time.time) -> None:
        self.headsets_path, self.state_path, self.log, self.post, self.now = headsets_path, state_path, log, post, now
        self.command = command or ["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"]
        self.url = ""
        self.posted = {}   # token -> (url, when): what each headset's notice says

    def _state(self, url: str) -> None:
        self.url = url
        if self.state_path is None:
            return
        if url:
            write_private(self.state_path, json.dumps({"url": url}))
        else:
            self.state_path.unlink(missing_ok=True)

    async def publish(self) -> int:
        """Post the address for every headset whose notice is missing, old, or says another address
        (a headset paired a minute ago has none yet). Returns how many were posted."""
        if not self.url:
            return 0
        done = 0
        for token in load_headsets(self.headsets_path).values():
            url, when = self.posted.get(token, ("", 0))
            if url == self.url and self.now() - when < REPOST_S:
                continue
            ts = int(self.now())
            try:
                await asyncio.get_running_loop().run_in_executor(None, self.post, topic(token), notice(self.url, token, ts))
            except OSError as e:
                self.log(f"remote: could not post the address ({e}), trying again")
                continue
            self.posted[token] = (self.url, ts)
            done += 1
        return done

    async def run(self, retry_s: float = 5, publish_every_s: float = 20) -> None:
        if shutil.which(self.command[0]) is None:
            self.log(f"remote: {self.command[0]} is not installed (brew install cloudflared): this laptop stays "
                     "reachable on its own Wi-Fi only")
            return
        try:
            while True:
                proc = await asyncio.create_subprocess_exec(
                    *self.command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
                posting = None
                try:
                    async for line in proc.stderr:
                        found = TUNNEL_URL.search(line)
                        if found and not self.url:
                            self._state("wss://" + found.group().decode()[len("https://"):])
                            self.log(f"remote: reachable from other networks at {self.url}")
                            posting = asyncio.ensure_future(self._keep_published(publish_every_s))
                    await proc.wait()
                finally:
                    if posting:
                        posting.cancel()
                    if proc.returncode is None:
                        proc.terminate()
                        await proc.wait()
                    self._state("")
                self.log(f"remote: the tunnel ended ({proc.returncode}), starting it again")
                await asyncio.sleep(retry_s)
        finally:
            self._state("")

    async def _keep_published(self, every_s: float) -> None:
        while True:
            posted = await self.publish()
            if posted:
                self.log(f"remote: address posted for {posted} headset(s)")
            await asyncio.sleep(every_s)


def shown_remote(path: Path = REMOTE_STATE) -> str:
    """The running gateway's public host name, or "" when it is not reachable from other networks."""
    try:
        return json.loads(path.read_text())["url"].split("://", 1)[1]
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return ""


# ---------------------------------------------------------------- HTTP head
def parse_head(head: bytes):
    """(method, target, version, [(name, value)]) of a request head, or None if it is not one."""
    try:
        lines = head.decode("latin-1").split("\r\n")
        method, target, version = lines[0].split(" ")
        headers = []
        for line in lines[1:]:
            if not line:
                continue
            name, value = line.split(":", 1)
            headers.append((name.strip(), value.strip()))
    except ValueError:
        return None
    if not version.startswith("HTTP/1.") or not target.startswith("/"):
        return None
    return method, target, version, headers


def route(target: str, routes: dict = ROUTES):
    """'/daemon/api/x?y' -> ('daemon', 8000, '/api/x?y'); None for an unknown first part."""
    path, sep, query = target.partition("?")
    name, _, rest = path[1:].partition("/")
    if name not in routes:
        return None
    return name, routes[name], "/" + rest + sep + query


def bearer(headers) -> str:
    for name, value in headers:
        if name.lower() == "authorization" and value[:7].lower() == "bearer ":
            return value[7:].strip()
    return ""


def upstream_head(method: str, target: str, version: str, headers, port: int) -> bytes:
    """The head as the local service gets it: its own path and Host, no token, and one request per connection
    unless it becomes a WebSocket (so every request passes the token check). What the tunnel added on the way
    (cf-ray, x-forwarded-for, ...) is dropped: the headset has shown its token here, and a service behind this
    door must not take it for a stranger from the internet (the backend asked such clients for an HF sign-in)."""
    upgrade = any(n.lower() == "upgrade" for n, _ in headers)
    out = [f"{method} {target} {version}", f"Host: 127.0.0.1:{port}"]
    for name, value in headers:
        low = name.lower()
        if low in ("host", "authorization", "cdn-loop") or (low == "connection" and not upgrade):
            continue
        if low.startswith(("cf-", "x-forwarded-")):
            continue
        out.append(f"{name}: {value}")
    if not upgrade:
        out.append("Connection: close")
    return ("\r\n".join(out) + "\r\n\r\n").encode("latin-1")


def refusal(status: str, text: str) -> bytes:
    return answer(status, text.encode(), "text/plain; charset=utf-8")


def answer(status: str, body: bytes, kind: str = "application/json") -> bytes:
    return (f"HTTP/1.1 {status}\r\nContent-Type: {kind}\r\nContent-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n").encode() + body


# ---------------------------------------------------------------- forwarding
async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Copy until the reader ends, then end the writer too: politely after a clean end, at once after a broken one
    (a headset that dropped off the Wi-Fi must not leave its connection to the daemon open)."""
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except (ConnectionError, OSError):
        writer.close()


class Gateway:
    def __init__(self, routes: dict = ROUTES, headsets_path: Path = HEADSETS, log=print, pairing=None,
                 identity: dict = None) -> None:
        self.routes = routes
        self.path = headsets_path
        self.log = log
        self.pairing = pairing      # None: no pairing by code, headsets only with `add`
        self.identity = identity or {}   # {"id", "name"} of this laptop, told to a headset that paired
        self.via = ""   # set on the door behind the tunnel: every peer there is 127.0.0.1, the log says "remote"
        self._stamp = None
        self._headsets = {}

    def headsets(self) -> dict:
        """The file is read again when it changed, so a new headset works without a restart."""
        try:
            stamp = self.path.stat().st_mtime_ns
        except OSError:
            stamp = 0
        if stamp != self._stamp:
            self._stamp, self._headsets = stamp, load_headsets(self.path)
        return self._headsets

    async def handle(self, client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        peer = self.via or (client_w.get_extra_info("peername") or ("?",))[0]
        try:
            await self._serve(peer, client_r, client_w)
        except (ConnectionError, OSError, asyncio.CancelledError):
            pass
        finally:
            client_w.close()

    async def _refuse(self, writer: asyncio.StreamWriter, status: str, text: str) -> None:
        writer.write(refusal(status, text))
        await writer.drain()

    async def _serve(self, peer: str, client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(client_r.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT_S)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            return
        request = parse_head(head[:-4])
        if request is None:
            return await self._refuse(client_w, "400 Bad Request", "not an HTTP request")
        method, target, version, headers = request
        if method == "POST" and target == "/pair" and self.pairing is not None:
            return await self._pair(peer, client_r, client_w, headers)
        name = headset_of(bearer(headers), self.headsets())
        if name is None:
            self.log(f"{peer}: refused, no known token ({method} {target.partition('?')[0]})")
            return await self._refuse(client_w, "401 Unauthorized", "this headset is not set up for this laptop")
        found = route(target, self.routes)
        if found is None:
            return await self._refuse(client_w, "404 Not Found", "unknown path")
        part, port, upstream_target = found
        try:
            up_r, up_w = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            return await self._refuse(client_w, "502 Bad Gateway", f"{part} does not answer on this laptop")
        long_lived = any(n.lower() == "upgrade" for n, _ in headers)
        if long_lived:
            self.log(f"{peer} {name} {part}: connected")
        try:
            up_w.write(upstream_head(method, upstream_target, version, headers, port))
            await up_w.drain()
            to_headset = asyncio.ensure_future(pipe(up_r, client_w))
            to_service = asyncio.ensure_future(pipe(client_r, up_w))
            # The answer decides when it is over: a headset that stops sending may still be waiting for it
            await asyncio.wait([to_headset])
            to_service.cancel()
        finally:
            up_w.close()
            if long_lived:
                self.log(f"{peer} {name} {part}: closed")


    async def _pair(self, peer: str, client_r, client_w, headers) -> None:
        try:
            length = int(next((v for n, v in headers if n.lower() == "content-length"), "0"))
            if not 0 < length <= 1024:
                raise ValueError
            body = json.loads(await asyncio.wait_for(client_r.readexactly(length), HEAD_TIMEOUT_S))
            code, name = body.get("code"), body.get("name")
        except (ValueError, AttributeError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            return await self._refuse(client_w, "400 Bad Request", "expected {\"code\", \"name\"}")
        status, reply = self.pairing.pair(code, name)
        if status.startswith("200"):
            reply.update(self.identity)
            self.log(f"{peer}: paired as {headset_name(name)}")
        else:
            self.log(f"{peer}: pairing refused, {reply['error']}")
        client_w.write(answer(status, json.dumps(reply).encode()))
        await client_w.drain()


# ---------------------------------------------------------------- command line
def wifi_address() -> str:
    try:
        return subprocess.run(["ipconfig", "getifaddr", "en0"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def adb_line(token: str, port: int = PORT) -> str:
    return (f"adb shell am start -n {APP} \\\n"
            f"    -e gateway ws://{wifi_address() or '<laptop-ip>'}:{port} -e token {token}")


async def serve(host: str, port: int, remote: bool = False) -> None:
    pairing = Pairing()
    identity = {"id": laptop_id(), "name": laptop_name()}
    say = lambda line: print(line, flush=True)   # noqa: E731
    gateway = Gateway(log=say, pairing=pairing, identity=identity)
    loop = asyncio.get_running_loop()
    tunnel = None
    try:
        server = await asyncio.start_server(gateway.handle, host, port)
        if remote:   # a second door for the tunnel, on this machine only and without pairing
            public = Gateway(log=say)
            public.via = "remote"
            await asyncio.start_server(public.handle, "127.0.0.1", PUBLIC_PORT)
            tunnel = asyncio.ensure_future(Remote(log=say).run())
        await loop.create_datagram_endpoint(
            lambda: Discovery(discovery_answer(port, identity["id"], identity["name"])),
            local_addr=(host, port), allow_broadcast=True)
    except OSError as e:
        sys.exit(f"gateway: cannot listen on {host}:{port} ({e.strerror or e}). Is another start_meeting.sh still running?")
    print(f"gateway {host}:{port}, {len(gateway.headsets())} headset(s) known", flush=True)
    try:
        async with server:
            while True:   # a code that ran out is replaced, so the status view always shows one that works
                pairing.current()
                await asyncio.sleep(5)
    finally:
        PAIRING_CODE.unlink(missing_ok=True)   # no gateway, no code
        if tunnel:
            tunnel.cancel()
            await asyncio.gather(tunnel, return_exceptions=True)   # lets it stop cloudflared and clear its state


def shown_code(path: Path = PAIRING_CODE, now=time.time) -> str:
    """The running gateway's code as "123 456", or "" when there is none that still works."""
    try:
        data = json.loads(path.read_text())
        code = data["code"] if data["until"] > now() else ""
    except (OSError, ValueError, KeyError, TypeError):
        return ""
    return f"{code[:3]} {code[3:]}" if code else ""


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="The one door from the headset to this laptop (see the top of the file)")
    sub = ap.add_subparsers(dest="command")
    run = sub.add_parser("run", help="run the gateway (the default)")
    for p in (ap, run):
        p.add_argument("--host", default="0.0.0.0")
        p.add_argument("--port", type=int, default=PORT)
        p.add_argument("--remote", action="store_true",
                       help="also reachable from other networks (Cloudflare quick tunnel, address posted on ntfy.sh)")
    sub.add_parser("code", help="the pairing code of the running gateway, to type into a new headset")
    sub.add_parser("remote", help="is the running gateway reachable from other networks?")
    sub.add_parser("add", help="new headset without the code: prints its one-time adb line").add_argument("name")
    sub.add_parser("remove", help="this headset's token stops working").add_argument("name")
    sub.add_parser("list", help="names of the headsets that may connect")
    args = ap.parse_args(argv)

    if args.command == "add":
        print(f"With the headset \"{args.name}\" on adb, once:\n\n{adb_line(add_headset(args.name), args.port)}\n")
    elif args.command == "remove":
        headsets = load_headsets()
        if headsets.pop(args.name, None) is None:
            sys.exit(f"no headset named \"{args.name}\"; known: {', '.join(headsets) or 'none'}")
        save_headsets(headsets)
        print(f"\"{args.name}\" removed")
    elif args.command == "list":
        print("\n".join(load_headsets()) or "no headsets yet")
    elif args.command == "remote":
        print(shown_remote() or "off")
    elif args.command == "code":
        print(shown_code() or "no code: the gateway is not running (./start_meeting.sh starts it)")
    else:
        import signal
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))   # start_meeting.sh stops it this way: tidy up too
        try:
            asyncio.run(serve(args.host, args.port, args.remote))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
