"""Daemon API on the Wi-Fi for the Unity app: forwards <wifi-ip>:8000 to the daemon on 127.0.0.1:8000.

Since reachy_mini 1.14 (desktop app update, 2026-11-06) the daemon binds its HTTP API to 127.0.0.1 on the Lite, so
the headset cannot reach port 8000 any more (video signalling on 8443 still listens on all interfaces). This keeps
Reachy Mini Control open and in charge of the daemon. Standard library only, any python3:

    python3 robot/scripts/daemon_lan_relay.py              # Wi-Fi address from en0
    python3 robot/scripts/daemon_lan_relay.py 192.168.1.5  # or a given one

The API has no password: while this runs, anyone on the Wi-Fi can move Reachy. Ctrl+C closes it again.
"""

import asyncio
import subprocess
import sys

PORT = 8000


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
    peer = client_w.get_extra_info("peername")
    try:
        daemon_r, daemon_w = await asyncio.open_connection("127.0.0.1", PORT)
    except OSError as e:
        print(f"{peer}: daemon unreachable ({e}), is Reachy Mini Control running?", flush=True)
        client_w.close()
        return
    print(f"{peer}: connected", flush=True)
    await asyncio.gather(pipe(client_r, daemon_w), pipe(daemon_r, client_w))
    print(f"{peer}: closed", flush=True)


async def main(host: str) -> None:
    server = await asyncio.start_server(handle, host, PORT)
    print(f"relay {host}:{PORT} -> 127.0.0.1:{PORT}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else subprocess.run(
        ["ipconfig", "getifaddr", "en0"], capture_output=True, text=True).stdout.strip()
    if not host:
        sys.exit("no Wi-Fi address on en0: pass the address as an argument")
    try:
        asyncio.run(main(host))
    except KeyboardInterrupt:
        pass
