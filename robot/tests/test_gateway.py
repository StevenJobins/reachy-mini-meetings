"""The gateway (robot/scripts/gateway.py) against stand-in services on 127.0.0.1: no robot, no daemon.   pytest -q"""

import asyncio
import importlib.util
import stat
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("gateway", Path(__file__).parents[1] / "scripts" / "gateway.py")
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


# ---------------------------------------------------------------- pure parts
def test_route_strips_the_first_part_and_keeps_the_query():
    assert gateway.route("/daemon/api/state/ws/full?frequency=60") == ("daemon", 8000, "/api/state/ws/full?frequency=60")
    assert gateway.route("/signalling") == ("signalling", 8443, "/")
    assert gateway.route("/backend/") == ("backend", 8766, "/")
    assert gateway.route("/time") == ("time", 8444, "/")


@pytest.mark.parametrize("target", ["/", "/api/daemon/status", "/daemonx/api", "/Daemon/api", "/../daemon"])
def test_route_knows_only_its_four_parts(target):
    assert gateway.route(target) is None


def test_headset_of_needs_the_exact_token():
    headsets = {"Kevin": "abc123", "Dominic": "xyz789"}
    assert gateway.headset_of("xyz789", headsets) == "Dominic"
    assert gateway.headset_of("abc12", headsets) is None
    assert gateway.headset_of("", headsets) is None
    assert gateway.headset_of("abc123", {}) is None


def test_upstream_head_drops_the_token_and_closes_after_one_request():
    headers = [("Host", "192.168.1.5:8790"), ("Authorization", "Bearer abc123"), ("Connection", "keep-alive"),
               ("Accept", "*/*")]
    head = gateway.upstream_head("POST", "/api/move/play/wake_up", "HTTP/1.1", headers, 8000).decode()
    assert head.startswith("POST /api/move/play/wake_up HTTP/1.1\r\nHost: 127.0.0.1:8000\r\n")
    assert "abc123" not in head and "192.168.1.5" not in head and "keep-alive" not in head
    assert "Accept: */*\r\n" in head and head.endswith("Connection: close\r\n\r\n")


def test_upstream_head_keeps_a_websocket_upgrade():
    headers = [("Host", "x"), ("Upgrade", "websocket"), ("Connection", "Upgrade"), ("Sec-WebSocket-Key", "k")]
    head = gateway.upstream_head("GET", "/", "HTTP/1.1", headers, 8766).decode()
    assert "Upgrade: websocket\r\n" in head and "Connection: Upgrade\r\n" in head and "Sec-WebSocket-Key: k\r\n" in head
    assert "Connection: close" not in head


def test_upstream_head_drops_what_the_tunnel_added():
    headers = [("Host", "x.trycloudflare.com"), ("Cf-Ray", "8e1a-ZRH"), ("CF-Connecting-IP", "203.0.113.7"),
               ("X-Forwarded-For", "203.0.113.7"), ("X-Forwarded-Proto", "https"), ("Cdn-Loop", "cloudflare"),
               ("Upgrade", "websocket"), ("Connection", "Upgrade"), ("Sec-WebSocket-Key", "k")]
    head = gateway.upstream_head("GET", "/", "HTTP/1.1", headers, 8766).decode().lower()
    assert "cf-" not in head and "x-forwarded" not in head and "cdn-loop" not in head and "203.0.113.7" not in head
    assert "upgrade: websocket" in head and "sec-websocket-key: k" in head


def test_parse_head_refuses_what_is_not_http():
    assert gateway.parse_head(b"GET /time HTTP/1.1\r\nHost: a\r\nX: 1: 2")[3] == [("Host", "a"), ("X", "1: 2")]
    assert gateway.parse_head(b"\x16\x03\x01 binary") is None
    assert gateway.parse_head(b"GET http://evil/ HTTP/1.1") is None
    assert gateway.parse_head(b"GET /x HTTP/1.1\r\nno colon here") is None


def test_headsets_file_is_private_and_a_second_add_replaces_the_token(tmp_path):
    path = tmp_path / "config" / "headsets.json"
    first = gateway.add_headset("Kevin's XR", path)
    second = gateway.add_headset("Kevin's XR", path)
    assert first != second and len(second) >= 32
    assert gateway.load_headsets(path) == {"Kevin's XR": second}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_broken_headsets_file_lets_nobody_in(tmp_path):
    path = tmp_path / "headsets.json"
    path.write_text("[1, 2")
    assert gateway.load_headsets(path) == {}
    path.write_text('{"a": "", "b": 5, "c": "tok"}')
    assert gateway.load_headsets(path) == {"c": "tok"}


# ---------------------------------------------------------------- through real sockets
class Service:
    """Stands in for the daemon: remembers the head it got; answers a plain request once and closes, and echoes
    after an upgrade."""

    def __init__(self):
        self.heads = []

    async def handle(self, r, w):
        head = (await r.readuntil(b"\r\n\r\n")).decode()
        self.heads.append(head)
        if "Upgrade: websocket" in head:
            w.write(b"HTTP/1.1 101 Switching Protocols\r\n\r\n")
            while data := await r.read(100):
                w.write(b"echo:" + data)
                await w.drain()
        else:
            body = (await r.readexactly(4)).decode() if "Content-Length: 4" in head else ""
            w.write(f"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nok{body}".encode())
            await w.drain()
        w.close()


async def setup(tmp_path):
    service, log = Service(), []
    upstream = await asyncio.start_server(service.handle, "127.0.0.1", 0)
    path = tmp_path / "headsets.json"
    token = gateway.add_headset("test headset", path)
    routes = {"daemon": upstream.sockets[0].getsockname()[1], "backend": 1}   # port 1: nothing listens
    front = await asyncio.start_server(gateway.Gateway(routes, path, log.append).handle, "127.0.0.1", 0)
    return service, log, token, path, front.sockets[0].getsockname()[1], (upstream, front)


async def ask(port, request: bytes) -> bytes:
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(request)
    reply = await asyncio.wait_for(r.read(-1), 5)
    w.close()
    return reply


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


def test_without_a_token_nothing_reaches_the_service(tmp_path):
    async def go():
        service, log, token, _, port, _ = await setup(tmp_path)
        for auth in ("", "Authorization: Bearer wrong\r\n", f"Authorization: Basic {token}\r\n",
                     f"X-Token: {token}\r\n"):
            reply = await ask(port, f"POST /daemon/api/move/play/wake_up HTTP/1.1\r\nHost: x\r\n{auth}\r\n".encode())
            assert reply.startswith(b"HTTP/1.1 401 "), auth
        assert service.heads == []
        assert len(log) == 4 and all("refused" in line and token not in line for line in log)
    run(go())


def test_token_in_the_address_does_not_count(tmp_path):
    async def go():
        service, _, token, _, port, _ = await setup(tmp_path)
        reply = await ask(port, f"GET /daemon/api/daemon/status?token={token} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        assert reply.startswith(b"HTTP/1.1 401 ") and service.heads == []
    run(go())


def test_request_with_a_token_is_forwarded_with_its_body(tmp_path):
    async def go():
        service, _, token, _, port, _ = await setup(tmp_path)
        reply = await ask(port, (f"POST /daemon/api/move/play/wake_up?x=1 HTTP/1.1\r\nHost: 10.0.0.2:8790\r\n"
                                 f"Authorization: Bearer {token}\r\nContent-Length: 4\r\n\r\nbody").encode())
        assert reply == b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nokbody"
        head = service.heads[0]
        assert head.startswith("POST /api/move/play/wake_up?x=1 HTTP/1.1\r\n")
        assert token not in head and "Connection: close" in head
    run(go())


def test_websocket_goes_both_ways_and_is_logged(tmp_path):
    async def go():
        service, log, token, _, port, _ = await setup(tmp_path)
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write((f"GET /daemon/api/move/ws/set_target HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n"
                 f"Connection: Upgrade\r\nAuthorization: Bearer {token}\r\n\r\n").encode())
        assert await r.readuntil(b"\r\n\r\n") == b"HTTP/1.1 101 Switching Protocols\r\n\r\n"
        for message in (b"one", b"two"):
            w.write(message)
            assert await asyncio.wait_for(r.readexactly(5 + len(message)), 5) == b"echo:" + message
        assert log == ["127.0.0.1 test headset daemon: connected"]
        w.close()
        for _ in range(100):
            if len(log) == 2:
                break
            await asyncio.sleep(0.02)
        assert log[1] == "127.0.0.1 test headset daemon: closed"
    run(go())


def test_unknown_path_and_silent_service_are_told_apart(tmp_path):
    async def go():
        _, _, token, _, port, _ = await setup(tmp_path)
        auth = f"Authorization: Bearer {token}\r\n"
        assert (await ask(port, f"GET /elsewhere HTTP/1.1\r\n{auth}\r\n".encode())).startswith(b"HTTP/1.1 404 ")
        reply = await ask(port, f"GET /backend HTTP/1.1\r\n{auth}\r\n".encode())
        assert reply.startswith(b"HTTP/1.1 502 ") and reply.endswith(b"backend does not answer on this laptop")
    run(go())


def test_removed_headset_is_out_without_a_restart(tmp_path):
    async def go():
        service, _, token, path, port, _ = await setup(tmp_path)
        request = f"GET /daemon/api/daemon/status HTTP/1.1\r\nAuthorization: Bearer {token}\r\n\r\n".encode()
        assert (await ask(port, request)).startswith(b"HTTP/1.1 200 ")
        gateway.save_headsets({}, path)
        assert (await ask(port, request)).startswith(b"HTTP/1.1 401 ")
        new = gateway.add_headset("second", path)
        assert (await ask(port, request.replace(token.encode(), new.encode()))).startswith(b"HTTP/1.1 200 ")
        assert len(service.heads) == 2
    run(go())


def test_taken_port_ends_with_one_plain_line():
    async def go():
        taken = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        with pytest.raises(SystemExit) as stop:
            await gateway.serve("127.0.0.1", taken.sockets[0].getsockname()[1])
        assert "cannot listen on 127.0.0.1:" in str(stop.value) and "start_meeting.sh" in str(stop.value)
    run(go())


def test_garbage_and_silence_get_no_service(tmp_path, monkeypatch):
    async def go():
        monkeypatch.setattr(gateway, "HEAD_TIMEOUT_S", 0.2)
        service, _, _, _, port, _ = await setup(tmp_path)
        assert (await ask(port, b"\x16\x03\x01\x02\x00\r\n\r\n")).startswith(b"HTTP/1.1 400 ")
        assert await ask(port, b"GET /daemon/api") == b""   # a head that never ends: closed after the timeout
        assert service.heads == []
    run(go())


# ---------------------------------------------------------------- pairing by code
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def new_pairing(tmp_path):
    clock = Clock()
    return gateway.Pairing(tmp_path / "headsets.json", tmp_path / "pairing_code.json", clock), clock


def test_right_code_gives_a_token_once(tmp_path):
    pairing, _ = new_pairing(tmp_path)
    code = pairing.current()
    assert len(code) == 6 and code.isdigit()
    status, reply = pairing.pair(code, "SM-I610 a3f2")
    assert status == "200 OK"
    assert gateway.load_headsets(tmp_path / "headsets.json") == {"SM-I610 a3f2": reply["token"]}
    assert pairing.current() != code                       # used: there is a new one
    assert pairing.pair(code, "second")[0] == "403 Forbidden"
    assert list(gateway.load_headsets(tmp_path / "headsets.json")) == ["SM-I610 a3f2"]


def test_code_runs_out(tmp_path):
    pairing, clock = new_pairing(tmp_path)
    code = pairing.current()
    clock.t += gateway.CODE_S - 1
    assert pairing.current() == code
    clock.t += 2
    assert pairing.pair(code, "late")[0] == "403 Forbidden"
    assert gateway.load_headsets(tmp_path / "headsets.json") == {}


def test_five_wrong_codes_pause_and_change_the_code(tmp_path):
    pairing, clock = new_pairing(tmp_path)
    code = pairing.current()
    wrong = f"{(int(code) + 1) % 10**6:06d}"
    for _ in range(gateway.WRONG_CODES):
        assert pairing.pair(wrong, "guess")[0] == "403 Forbidden"
    assert pairing.pair(code, "guess")[0] == "429 Too Many Requests"   # even the code that was right
    assert pairing.code != code
    clock.t += gateway.PAUSE_S + 1
    assert pairing.pair(code, "guess")[0] == "403 Forbidden"   # the old code is one more wrong guess
    assert pairing.pair(pairing.current(), "honest")[0] == "200 OK"


@pytest.mark.parametrize("code", [None, 123456, "", "12345", "1234567", ["123456"]])
def test_what_is_not_the_code_is_wrong(tmp_path, code):
    pairing, _ = new_pairing(tmp_path)
    pairing.current()
    assert pairing.pair(code, "x")[0] == "403 Forbidden"
    assert gateway.load_headsets(tmp_path / "headsets.json") == {}


def test_shown_code_only_while_it_works(tmp_path):
    pairing, clock = new_pairing(tmp_path)
    code = pairing.current()
    path = tmp_path / "pairing_code.json"
    assert gateway.shown_code(path, clock) == f"{code[:3]} {code[3:]}"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    clock.t += gateway.CODE_S + 1
    assert gateway.shown_code(path, clock) == ""
    assert gateway.shown_code(tmp_path / "missing.json", clock) == ""


def test_headset_names_are_made_safe():
    assert gateway.headset_name("  SM-I610 a3f2\n") == "SM-I610 a3f2"
    assert gateway.headset_name("x" * 100) == "x" * 40
    assert gateway.headset_name("\x1b[31m\x00") == "[31m"
    assert gateway.headset_name(None) == gateway.headset_name("") == gateway.headset_name(5) == "headset"


def test_laptop_id_stays(tmp_path):
    first = gateway.laptop_id(tmp_path / "laptop_id")
    assert len(first) == 16 and gateway.laptop_id(tmp_path / "laptop_id") == first


def test_pairing_over_http_then_the_token_opens_the_door(tmp_path):
    async def go():
        service, log = Service(), []
        upstream = await asyncio.start_server(service.handle, "127.0.0.1", 0)
        pairing, _ = new_pairing(tmp_path)
        door = gateway.Gateway({"daemon": upstream.sockets[0].getsockname()[1]}, tmp_path / "headsets.json", log.append,
                               pairing, {"id": "abc", "name": "Test Mac"})
        front = await asyncio.start_server(door.handle, "127.0.0.1", 0)
        port = front.sockets[0].getsockname()[1]

        def post(body: bytes) -> bytes:
            return b"POST /pair HTTP/1.1\r\nHost: x\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body

        import json
        wrong = await ask(port, post(b'{"code": "no", "name": "XR"}'))
        assert wrong.startswith(b"HTTP/1.1 403 ") and b"token" not in wrong
        assert (await ask(port, post(b"not json"))).startswith(b"HTTP/1.1 400 ")
        assert (await ask(port, b"POST /pair HTTP/1.1\r\nContent-Length: 999999\r\n\r\n")).startswith(b"HTTP/1.1 400 ")
        assert (await ask(port, b"GET /pair HTTP/1.1\r\n\r\n")).startswith(b"HTTP/1.1 401 ")   # only POST pairs

        body = json.dumps({"code": pairing.current(), "name": "XR"}).encode()
        right = await ask(port, post(body))
        assert right.startswith(b"HTTP/1.1 200 ")
        reply = json.loads(right.split(b"\r\n\r\n", 1)[1])
        assert reply["id"] == "abc" and reply["name"] == "Test Mac"
        status = await ask(port, f"GET /daemon/api/daemon/status HTTP/1.1\r\nAuthorization: Bearer {reply['token']}\r\n\r\n".encode())
        assert status.startswith(b"HTTP/1.1 200 ")
        assert (await ask(port, post(body))).startswith(b"HTTP/1.1 403 ")   # the code is used up
        assert not any(reply["token"] in line or pairing.code in line for line in log)
    run(go())


def test_without_pairing_the_pair_path_is_closed(tmp_path):
    async def go():
        _, _, _, _, port, _ = await setup(tmp_path)
        body = b'{"code": "123456", "name": "XR"}'
        reply = await ask(port, b"POST /pair HTTP/1.1\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        assert reply.startswith(b"HTTP/1.1 401 ")
    run(go())


def test_discovery_answers_only_its_question():
    async def go():
        loop = asyncio.get_running_loop()
        answer = gateway.discovery_answer(8790, "abc", "Kevin's MacBook Pro")
        server, _ = await loop.create_datagram_endpoint(lambda: gateway.Discovery(answer), local_addr=("127.0.0.1", 0))
        port = server.get_extra_info("sockname")[1]
        got = asyncio.Queue()

        class Client(asyncio.DatagramProtocol):
            def datagram_received(self, data, addr):
                got.put_nowait(data)

        client, _ = await loop.create_datagram_endpoint(Client, remote_addr=("127.0.0.1", port))
        client.sendto(b"who is there?")
        client.sendto(b"reachy-meetings?\n")
        assert await asyncio.wait_for(got.get(), 5) == b"reachy-meetings 1 8790 abc Kevin's MacBook Pro"
        await asyncio.sleep(0.05)
        assert got.empty()
        server.close(); client.close()
    run(go())


# ---------------------------------------------------------------- from another network
URL = "wss://quiet-river-1234.trycloudflare.com"


def test_notice_is_read_only_with_the_same_token():
    text = gateway.notice(URL, "token-a", 1_000_000)
    assert gateway.read_notice(text, "token-a", lambda: 1_000_100) == URL
    assert gateway.read_notice(text, "token-b", lambda: 1_000_100) is None
    assert gateway.read_notice(text.replace("quiet", "loud"), "token-a", lambda: 1_000_100) is None   # address changed
    assert gateway.read_notice(text.replace("1000000", "1000001"), "token-a", lambda: 1_000_100) is None
    assert gateway.read_notice(text, "token-a", lambda: 1_000_000 + gateway.NOTICE_MAX_AGE_S + 1) is None


def test_notice_never_leads_to_an_unencrypted_address():
    for url in ("ws://evil.example", "https://evil.example", "http://192.168.1.5:8790"):
        assert gateway.read_notice(gateway.notice(url, "token-a", 1_000_000), "token-a", lambda: 1_000_100) is None
    for junk in (None, "", "wss://x", "wss://x 1 2 3", 5):
        assert gateway.read_notice(junk, "token-a") is None


def test_topic_is_per_headset_and_gives_no_token_away():
    a, b = gateway.topic("token-a"), gateway.topic("token-b")
    assert a != b and a == gateway.topic("token-a")
    assert a.startswith("rm-") and len(a) == 43 and "token" not in a
    # fixed values: RemoteAddressTests.cs in the app checks the same ones
    assert gateway.topic("s3cret-token") == "rm-" + gateway._mac("s3cret-token", "reachy-meetings address")[:40]
    assert gateway.notice(URL, "s3cret-token", 1763000000).split(" ")[:2] == [URL, "1763000000"]


def fake_tunnel(tmp_path, lines: str):
    """Stands in for cloudflared: prints its lines on stderr and stays, like a tunnel that is up."""
    script = tmp_path / "tunnel.sh"
    script.write_text(f"#!/bin/sh\n{lines}\nexec sleep 30\n")
    script.chmod(0o755)
    return [str(script)]


def test_remote_posts_the_address_for_every_headset_also_one_added_later(tmp_path):
    async def go():
        headsets = tmp_path / "headsets.json"
        first = gateway.add_headset("first", headsets)
        posts, log = [], []
        remote = gateway.Remote(headsets_path=headsets, state_path=tmp_path / "remote.json", log=log.append,
                                command=fake_tunnel(tmp_path, 'echo "ERR failed https://api.trycloudflare.com/tunnel" >&2\n'
                                                              'echo "INF |  https://quiet-river-1234.trycloudflare.com  |" >&2'),
                                post=lambda topic, text: posts.append((topic, text)))
        running = asyncio.ensure_future(remote.run(retry_s=0.05, publish_every_s=0.05))
        for _ in range(100):
            if posts:
                break
            await asyncio.sleep(0.02)
        assert remote.url == URL                                  # not the api address from the error line
        assert gateway.shown_remote(tmp_path / "remote.json") == "quiet-river-1234.trycloudflare.com"
        assert posts[0][0] == gateway.topic(first) and gateway.read_notice(posts[0][1], first) == URL
        second = gateway.add_headset("second", headsets)
        for _ in range(100):
            if len(posts) > 1:
                break
            await asyncio.sleep(0.02)
        assert [p[0] for p in posts] == [gateway.topic(first), gateway.topic(second)]   # once each, not again and again
        assert not any(first in line or second in line for line in log)
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        assert gateway.shown_remote(tmp_path / "remote.json") == "" and remote.url == ""
    run(go())


def test_remote_starts_the_tunnel_again_and_posts_the_new_address(tmp_path):
    async def go():
        headsets = tmp_path / "headsets.json"
        token = gateway.add_headset("only", headsets)
        counter = tmp_path / "starts"
        script = tmp_path / "tunnel.sh"   # every start gets another address and ends at once, like a tunnel that drops
        script.write_text(f'#!/bin/sh\necho x >> {counter}\nn=$(wc -l < {counter} | tr -d " ")\n'
                          'echo "https://tunnel-$n.trycloudflare.com" >&2\nsleep 0.3\n')
        script.chmod(0o755)
        posts = []
        remote = gateway.Remote(headsets_path=headsets, state_path=None, log=lambda line: None, command=[str(script)],
                                post=lambda topic, text: posts.append(gateway.read_notice(text, token)))
        running = asyncio.ensure_future(remote.run(retry_s=0.05, publish_every_s=0.05))
        for _ in range(200):
            if len(posts) >= 2:
                break
            await asyncio.sleep(0.02)
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        assert posts[:2] == ["wss://tunnel-1.trycloudflare.com", "wss://tunnel-2.trycloudflare.com"]
    run(go())


def test_remote_without_the_tunnel_program_says_so_and_stops(tmp_path):
    async def go():
        log = []
        remote = gateway.Remote(headsets_path=tmp_path / "h.json", state_path=None, log=log.append,
                                command=["no-such-tunnel-program"])
        await asyncio.wait_for(remote.run(), 5)
        assert len(log) == 1 and "not installed" in log[0]
    run(go())


def test_a_failed_post_is_tried_again(tmp_path):
    async def go():
        headsets = tmp_path / "headsets.json"
        token = gateway.add_headset("only", headsets)
        calls, log = [], []

        def post(topic, text):
            calls.append(topic)
            if len(calls) == 1:
                raise OSError("no network")

        remote = gateway.Remote(headsets_path=headsets, state_path=None, log=log.append, post=post)
        remote.url = URL
        assert await remote.publish() == 0 and "could not post" in log[0]
        assert await remote.publish() == 1
        assert await remote.publish() == 0          # posted and fresh: nothing to do
        assert calls == [gateway.topic(token)] * 2
    run(go())


def test_the_door_behind_the_tunnel_does_not_pair(tmp_path):
    async def go():
        public = gateway.Gateway({"daemon": 1}, tmp_path / "headsets.json", lambda line: None)   # as serve() builds it
        public.via = "remote"
        front = await asyncio.start_server(public.handle, "127.0.0.1", 0)
        body = b'{"code": "123456", "name": "far away"}'
        reply = await ask(front.sockets[0].getsockname()[1],
                          b"POST /pair HTTP/1.1\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        assert reply.startswith(b"HTTP/1.1 401 ")
        assert gateway.load_headsets(tmp_path / "headsets.json") == {}
    run(go())
