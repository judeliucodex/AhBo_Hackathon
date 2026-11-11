"""Stand-in server for the browser-iPad tests: HTTP (static files, manifest, photo upload) and the
`/ws/device` WebSocket in one stdlib-only process, speaking CONTRACTS §2 like
`brain/ahbo/links/device_hub.py` does — but only the parts the sim exercises. The ahbo-browser image
ships no web framework, so this rolls its own HTTP parsing and RFC 6455 framing (server→client text
frames are never masked; client frames are). Not a pytest module: imported by tests/sim/conftest.py.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import struct
import uuid
from pathlib import Path

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
REPO = Path(__file__).resolve().parents[3]
CT = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".mjs": "text/javascript",
      ".css": "text/css", ".json": "application/json", ".wav": "audio/wav", ".m4a": "audio/mp4",
      ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml"}

# The fixed-clip manifest the stub serves: F1 really plays (the test generates tone.wav), F2's file is
# missing — the sim must still finish the line from the manifest's duration (CONTRACTS §2.2, PRD §10.3).
MANIFEST = {
    "version": "f-stub0001",
    "fallback": True,
    "clips": {
        "F1": {"file": "tone.wav", "text": "哎呀！我感應到你好似跌低咗！",
               "segments": [[0, 14, 0.0, 0.9]], "duration": 0.9},
        "F2": {"file": "missing.m4a", "text": "我而家影張相睇吓先。",
               "segments": [[0, 10, 0.0, 1.2]], "duration": 1.2},
    },
}

RE_VAL = r"^[A-Za-z0-9_.-]{1,128}$"


def ulid() -> str:
    return uuid.uuid4().hex.upper()[:26]


class WSConn:
    """One WebSocket connection (server side of RFC 6455, text messages only)."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader, self.writer = reader, writer
        self.closed = False

    async def send_text(self, s: str) -> None:
        await self._frame(0x1, s.encode("utf-8"))

    async def send_close(self, code: int) -> None:
        try:
            await self._frame(0x8, struct.pack(">H", code))
        except (ConnectionError, RuntimeError):
            pass

    async def _frame(self, opcode: int, payload: bytes) -> None:
        if self.closed:
            return
        head = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            head += bytes([n])
        elif n < 65536:
            head += bytes([126]) + n.to_bytes(2, "big")
        else:
            head += bytes([127]) + n.to_bytes(8, "big")
        self.writer.write(head + payload)
        await self.writer.drain()

    async def recv_message(self) -> str | None:
        """The next text message, or None when the peer closed (close frames are answered)."""
        buf = b""
        while True:
            b1 = (await self.reader.readexactly(1))[0]
            opcode = b1 & 0x0F
            b2 = (await self.reader.readexactly(1))[0]
            n = b2 & 0x7F
            if n == 126:
                n = int.from_bytes(await self.reader.readexactly(2), "big")
            elif n == 127:
                n = int.from_bytes(await self.reader.readexactly(8), "big")
            mask = await self.reader.readexactly(4) if b2 & 0x80 else b""
            payload = await self.reader.readexactly(n) if n else b""
            if mask and payload:
                payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
            if opcode == 0x8:                                   # close
                self.closed = True
                await self.send_close(1000)
                return None
            if opcode == 0x9:                                   # ping → pong
                await self._frame(0xA, payload)
                continue
            if opcode in (0xA, 0x2):                            # pong / binary: ignored
                continue
            buf += payload                                      # 0x1 text, 0x0 continuation
            if b1 & 0x80:
                return buf.decode("utf-8", "replace")


class StubHub:
    """Minimal stand-in for the DeviceHub: welcome + replay on hello, frames recorded for the tests."""

    def __init__(self) -> None:
        self.conn: WSConn | None = None
        self.frames: list[dict] = []                # every sim → server frame, in order
        self.hellos = 0
        self.reject_ws = False                      # True: accept then close 4401 (cookie expired)
        self.replay: list[dict] = [{"type": "reset"}, {"type": "mood", "mood": "alarm"}]

    async def handle(self, conn: WSConn) -> None:
        if self.reject_ws:
            await conn.send_close(4401)
            conn.closed = True
            return
        self.conn = conn
        while not conn.closed:
            raw = await conn.recv_message()
            if raw is None:
                break
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            self.frames.append(msg)
            await self._on(msg, conn)

    async def _on(self, msg: dict, conn: WSConn) -> None:
        if msg.get("type") == "hello":
            self.hellos += 1
            await conn.send_text(json.dumps({
                "id": ulid(), "type": "welcome", "device_token": "dt.stub.0.sig",
                "server_time": "2026-11-10T12:00:00.000+08:00",
                "fixed_cache_version": MANIFEST["version"], "text_sessions": True,
                "config": {"quiet_hours": {"start": "22:30", "end": "07:00"},
                           "interruption_enabled": True,
                           "wake": {"require_wai": True, "require_two_updates": True},
                           "reminder_mirror": {"date": "2026-11-10", "slots": []}},
            }, ensure_ascii=False))
            for ui in self.replay:
                await self.send_ui(ui)

    async def send_ui(self, ui: dict) -> None:
        await self.send_command({"type": "ui", "priority": 1, "ui": ui})

    async def send_command(self, frame: dict) -> None:
        """Server → sim command; the frame gets its own command id (CONTRACTS §2.2)."""
        if self.conn and not self.conn.closed:
            await self.conn.send_text(json.dumps({"id": ulid(), **frame}, ensure_ascii=False))

    async def send_command_twice(self, frame: dict) -> str:
        """Same frame, same id twice (the P0 resend path); returns the id."""
        cid = ulid()
        payload = json.dumps({"id": cid, **frame}, ensure_ascii=False)
        if self.conn and not self.conn.closed:
            await self.conn.send_text(payload)
            await self.conn.send_text(payload)
        return cid

    async def drop(self) -> None:
        """Kill the TCP connection the way a network failure would."""
        if self.conn and not self.conn.closed:
            self.conn.closed = True
            try:
                self.conn.writer.close()
            except (ConnectionError, RuntimeError):
                pass

    def of_type(self, t: str) -> list[dict]:
        return [f for f in self.frames if f.get("type") == t]


class StubServer:
    def __init__(self, media_dir: Path, face_dir: Path | None = None, sim_dir: Path | None = None):
        self.hub = StubHub()
        self.media_dir = media_dir
        self.face_dir = face_dir or REPO / "face"
        self.sim_dir = sim_dir or REPO / "sim"
        self.photo_uploads: list[dict] = []          # {incident_id, jpeg, size}
        self.fail_photo = False                      # True: /media/photo answers 500 (device reports error)
        self.manifest_requests = 0                   # how often the sim fetched the manifest
        self.server: asyncio.AbstractServer | None = None
        self.port = 0
        self.tasks: set[asyncio.Task] = set()
        # Cross-container runs: bind a routable interface and advertise the container's network alias
        # (AHBO_STUB_BIND / AHBO_STUB_HOST). Defaults serve a browser on the same host.
        self.bind_host = os.environ.get("AHBO_STUB_BIND", "127.0.0.1")
        self.host = os.environ.get("AHBO_STUB_HOST", "127.0.0.1")

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._client, self.bind_host, 0)
        self.port = self.server.sockets[0].getsockname()[1]
        self.url = f"http://{self.host}:{self.port}"

    async def stop(self) -> None:
        if self.server:
            self.server.close()
        for t in self.tasks:                       # keep-alive handlers parked in readuntil
            t.cancel()
        self.tasks.clear()
        if self.server:
            try:
                await asyncio.wait_for(self.server.wait_closed(), timeout=2)
            except (asyncio.TimeoutError, asyncio.CancelledError, OSError):
                pass
        if self.hub.conn and not self.hub.conn.closed:
            self.hub.conn.closed = True
            try:
                self.hub.conn.writer.close()
            except (ConnectionError, RuntimeError):
                pass

    # ------------------------------------------------------------------ connection loop
    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.tasks.add(task)
        try:
            while True:                                        # keep-alive: Chromium reuses connections
                head = await reader.readuntil(b"\r\n\r\n")
                lines = head.decode("latin1").split("\r\n")
                method, target, _ = lines[0].split(" ", 2)
                headers = {}
                for ln in lines[1:]:
                    if ":" in ln:
                        k, v = ln.split(":", 1)
                        headers[k.strip().lower()] = v.strip()
                body = b""
                clen = int(headers.get("content-length", "0") or 0)
                if clen:
                    body = await reader.readexactly(clen)
                path, _, query = target.partition("?")
                if "websocket" in headers.get("upgrade", "").lower():
                    await self._websocket(reader, writer, headers, path, query)
                    return
                status, ctype, payload = self._route(method, path, body, headers)
                reason = {200: "OK", 404: "Not Found", 405: "Method Not Allowed"}.get(status, "OK")
                resp = (f"HTTP/1.1 {status} {reason}\r\nContent-Type: {ctype}\r\n"
                        f"Content-Length: {len(payload)}\r\nCache-Control: no-store\r\nConnection: keep-alive\r\n\r\n")
                writer.write(resp.encode("latin1") + payload)
                await writer.drain()
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError, ValueError):
            pass
        finally:
            self.tasks.discard(task)
            try:
                writer.close()
            except (ConnectionError, RuntimeError):
                pass

    # ------------------------------------------------------------------ websocket upgrade
    async def _websocket(self, reader, writer, headers: dict, path: str, query: str) -> None:
        key = headers.get("sec-websocket-key", "")
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                      f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n").encode("latin1"))
        await writer.drain()
        conn = WSConn(reader, writer)
        self.hub.conn = conn
        try:
            await self.hub.handle(conn)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            conn.closed = True
            if self.hub.conn is conn:
                self.hub.conn = None

    # ------------------------------------------------------------------ http routes
    def _route(self, method: str, path: str, body: bytes, headers: dict):
        if path == "/ws/device":
            return 405, "text/plain", b"websocket only"
        if path == "/media/fixed/manifest.json" and method == "GET":
            self.manifest_requests += 1
            return 200, "application/json", json.dumps(MANIFEST, ensure_ascii=False).encode()
        if path.startswith("/media/fixed/") and method == "GET":
            return self._file(self.media_dir, path.rsplit("/", 1)[1])
        if path.startswith("/media/voice/") and method == "GET":
            return self._file(self.media_dir, path.rsplit("/", 1)[1])
        if path == "/media/photo" and method == "POST":
            if self.fail_photo:
                return 500, "application/json", b'{"ok": false, "reason": "storage"}'
            return self._photo(body, headers)
        if path.startswith("/sim/") or path == "/sim":
            name = path[4:].lstrip("/") or "index.html"
            return self._file(self.sim_dir, name)
        if path.startswith("/face/"):
            name = path[5:].lstrip("/") or "index.html"
            return self._file(self.face_dir, name)
        return 404, "application/json", b'{"ok": false}'

    def _file(self, base: Path, name: str):
        if not re.match(RE_VAL, name) or name.startswith("."):
            return 404, "application/json", b'{"ok": false}'
        p = (base / name).resolve()
        if base.resolve() not in p.parents or not p.is_file():
            return 404, "application/json", b'{"ok": false}'
        return 200, CT.get(p.suffix, "application/octet-stream"), p.read_bytes()

    def _photo(self, body: bytes, headers: dict):
        """Accept the multipart upload like routes/media.py: JPEG in, `{ok, photo_id}` out."""
        m = re.search(rb'name="incident_id"\r\n\r\n([A-Za-z0-9_]{1,64})', body)
        self.photo_uploads.append({
            "incident_id": m.group(1).decode() if m else None,
            "jpeg": body.find(b"\xff\xd8\xff") != -1,
            "size": len(body),
        })
        return 200, "application/json", b'{"ok": true, "photo_id": "ph_stub0001"}'
