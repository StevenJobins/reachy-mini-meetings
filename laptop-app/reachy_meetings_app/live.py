"""What happens in the meeting right now, for the window: the backend's messages (captions, who speaks), read from
its WebSocket on this laptop (ws://127.0.0.1:8766, protocol in backend/.../captions.py). Listens, and sends only what
the person at the laptop asks for with a button (send: the meeting notes as a mail draft), never a setting. Standard library only: the app's own Python has no websockets package, and the system's URL loading
would need a setting in the app bundle to talk to a plain ws:// address.

    Live(on_message).start()      on_message(dict) is called from the reader thread for every JSON message
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import threading
import time


def read_exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        part = sock.recv(n - len(data))
        if not part:
            raise ConnectionError("closed")
        data += part
    return data


def read_frame(sock: socket.socket) -> tuple[bool, int, bytes]:
    """(last piece of its message, opcode, payload) of the next frame from the server."""
    head = read_exact(sock, 2)
    fin, opcode, masked, n = bool(head[0] & 0x80), head[0] & 0x0F, bool(head[1] & 0x80), head[1] & 0x7F
    if n == 126:
        n = struct.unpack(">H", read_exact(sock, 2))[0]
    elif n == 127:
        n = struct.unpack(">Q", read_exact(sock, 8))[0]
    mask = read_exact(sock, 4) if masked else b""
    payload = read_exact(sock, n)
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return fin, opcode, payload


def frame(opcode: int, payload: bytes = b"") -> bytes:
    """A frame from us (clients mask what they send)."""
    mask = os.urandom(4)
    n = len(payload)
    head = bytes([0x80 | opcode]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n))
    return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


class Live(threading.Thread):
    def __init__(self, on_message, host: str = "127.0.0.1", port: int = 8766, retry_s: float = 2.0) -> None:
        super().__init__(daemon=True)
        self.on_message, self.host, self.port, self.retry_s = on_message, host, port, retry_s
        self.connected = False
        self.stopped = threading.Event()
        self.sock, self.writing = None, threading.Lock()

    def stop(self) -> None:
        self.stopped.set()

    def send(self, message: dict) -> bool:
        """One JSON message to the backend; False if it is not connected."""
        sock = self.sock
        if not (self.connected and sock):
            return False
        try:
            with self.writing:
                sock.sendall(frame(0x1, json.dumps(message).encode()))
            return True
        except OSError:
            return False

    def run(self) -> None:
        while not self.stopped.is_set():
            try:
                self.listen()
            except (OSError, ValueError):
                pass
            self.connected = False
            self.stopped.wait(self.retry_s)

    def listen(self) -> None:
        with socket.create_connection((self.host, self.port), timeout=5) as sock:
            key = base64.b64encode(os.urandom(16)).decode()
            sock.sendall((f"GET / HTTP/1.1\r\nHost: {self.host}:{self.port}\r\nUpgrade: websocket\r\n"
                          f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
            head = b""
            while b"\r\n\r\n" not in head:
                head += read_exact(sock, 1)
            if b" 101 " not in head.split(b"\r\n", 1)[0]:
                raise ConnectionError("no WebSocket there")
            sock.settimeout(60)   # the backend pings every 20 s: longer silence means it is gone
            self.sock, self.connected = sock, True
            text = b""
            while not self.stopped.is_set():
                fin, opcode, payload = read_frame(sock)
                if opcode == 0x9:        # ping: answer, or the backend drops us
                    with self.writing:
                        sock.sendall(frame(0xA, payload))
                elif opcode == 0x8:
                    return
                elif opcode == 0x1 or (opcode == 0x0 and text):
                    text += payload
                    if fin:
                        try:
                            message = json.loads(text)
                        except ValueError:
                            message = None
                        text = b""
                        if isinstance(message, dict):
                            self.on_message(message)
                # binary frames are the room sound: not for the window
