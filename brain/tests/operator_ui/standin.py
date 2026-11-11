"""A small stand-in for the operator API (CONTRACTS §9) used by the Playwright tests.

Implements the §9 endpoints with canned data and an SSE stream, and serves the REAL face kit from
`face/` so the live mirror renders truthfully. Pure stdlib (http.server + threads) so it runs in the
`ahbo-browser` image where the `ahbo` package is not installed. Request bodies are recorded for the
tests to assert on (which button POSTed what).
"""
from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

NOW = "2026-11-20T10:00:00.000+08:00"

MEDS_1200 = [{"id": "bp", "yue": "血壓藥", "en": "Amlodipine", "dose": "5 mg", "pill": ["--pill-b", "--pill-a"]},
             {"id": "dm", "yue": "糖尿藥", "en": "Metformin", "dose": "500 mg", "pill": ["--pill-a", "--pill-a"]},
             {"id": "chol", "yue": "膽固醇藥", "en": "Atorvastatin", "dose": "20 mg", "pill": ["--pill-a", "--pill-c"]},
             {"id": "asp", "yue": "薄血丸", "en": "Aspirin", "dose": "80 mg", "pill": ["--pill-c", "--pill-a"]}]

HEALTH_ROWS = [
    {"item": "database", "status": "green", "detail": "ahbo.sqlite"},
    {"item": "models", "status": "fake", "detail": "fake mode"},
    {"item": "telegram", "status": "fake", "detail": "fake mode"},
    {"item": "iPad connected", "status": "red", "detail": "0 device(s)"},
    {"item": "clock", "status": "green", "detail": "2026-11-20T10:00:00+08:00 (offset 0 s)"},
]

OUTBOX_ROWS = [
    {"id": "ob_t1", "ts": "2026-11-20T09:58:00.000+08:00", "channel": "telegram", "kind": "ALERT",
     "to_contact": "c_chun", "to_address": "fake:c_chun",
     "payload": {"text": "⚠️ 可能跌倒\n王婆婆可能有跌倒風險，請跟進。", "photo_id": None, "screenshots": [],
                 "contact_name": "阿珍"},
     "buttons": [{"id": "handled", "text": "我處理緊", "callback_data": "ob_t1:handled"},
                 {"id": "call_mum", "text": "打畀媽", "callback_data": "ob_t1:call_mum"}],
     "status": "queued", "fake": True, "correlation_id": "inc_test1", "provider_id": None,
     "updated_at": "2026-11-20T09:58:00.000+08:00"},
    {"id": "ob_c1", "ts": "2026-11-20T09:58:30.000+08:00", "channel": "twilio", "kind": "CALL",
     "to_contact": "c_chun", "to_address": "fake:+85290000001",
     "payload": {"twiml": "<?xml version=\"1.0\"?><Response><Gather><Say>呢度係阿寶，請撳 1</Say></Gather></Response>"},
     "buttons": [], "status": "in-progress", "fake": True, "correlation_id": "inc_test1",
     "provider_id": "CA_test", "updated_at": "2026-11-20T09:58:31.000+08:00"},
]

TRACE = {
    "events": [
        {"id": "evt_2", "ts": "2026-11-20T10:00:05.000+08:00", "type": "user.utterance", "source": "agent",
         "correlation_id": "turn_t1", "payload": {"text": "阿寶，我幾時食藥呀？", "stt_confidence": 0.94}},
        {"id": "evt_1", "ts": "2026-11-20T10:00:00.000+08:00", "type": "operator.reset", "source": "operator",
         "correlation_id": "inc_test1", "payload": {"by": "console"}},
    ],
    "commands": [
        {"id": "cmd_1", "ts": "2026-11-20T10:00:06.000+08:00", "type": "device.ui", "target": "device",
         "priority": 1, "correlation_id": "turn_t1", "payload": {"ui": {"type": "card"}}},
    ],
    "spans": [
        {"id": "sp_1", "start_ts": "2026-11-20T10:00:06.100+08:00", "name": "tool.get_med_status",
         "correlation_id": "turn_t1", "attrs": {"ms": 120}},
    ],
}

FIXED_CLIPS = {"version": "f-test", "clips": {"F1": {"text": "王婆婆，我係阿寶。", "duration": 1.9},
                                              "F2": {"text": "冇事嘅，我喺度。", "duration": 1.5}}}


def default_snapshot() -> dict[str, Any]:
    return {
        "now": NOW, "clock_offset_s": 0.0, "profile": "demo",
        "fake": ["livekit", "models", "speech", "telegram", "twilio", "vision"],
        "incident": None,
        "doses": [
            {"slot_id": "s_0800", "time": "08:00", "state": "taken_self_reported", "meds": [{"yue": "糖尿藥"}], "snoozes": 0},
            {"slot_id": "s_1200", "time": "12:00", "state": "pending", "meds": MEDS_1200, "snoozes": 0},
            {"slot_id": "s_2000", "time": "20:00", "state": "pending", "meds": [{"yue": "糖尿藥"}], "snoozes": 0},
        ],
        "actions": [], "deep_tasks": [], "session": None, "arbiter": {"queue": []},
        "devices": [{"device_id": "sim-1", "online": True, "last_seen": NOW, "battery": 0.87,
                     "thermal": "nominal", "wake_engine": "none", "app_version": "0.1.0",
                     "fixed_cache_version": "f-test", "ack_latency_ms": 42, "unacked_p0": 0, "sim": True}],
        "beacons": [{"id": "beacon1", "last_seen": NOW, "battery": 0.81, "rssi": -58, "last_seq": 42}],
        "spend": {"llm_usd": 0.0, "deep_tasks": 0, "twilio_calls": 1, "playwright_actions": 0, "sessions": 2},
        "caps": {"llm_usd": 1.5, "deep_tasks": 12, "twilio_calls": 20, "playwright_actions": 60, "sessions_per_day": 48},
        "knobs": {"flash_thinking": "off", "sonnet_thinking": "low", "interruption_enabled": True, "muted": False},
        "ui": {"mood": "idle", "cards": [], "steps": [], "ok_button": False, "offline": False},
        "components": {"installed": ["links"], "reducers": ["safety", "care"]},
    }


class StandIn:
    """Run with `with StandIn(face_dir) as s: ...` — `s.url` is the base (http://127.0.0.1:<port>)."""

    def __init__(self, face_dir: Path):
        self.face_dir = face_dir
        self.ui_dir = face_dir.parent / "operator-ui"      # the console's static files
        self.snapshot = default_snapshot()
        self.health_rows = [dict(r) for r in HEALTH_ROWS]
        self.outbox = [dict(r) for r in OUTBOX_ROWS]
        self.trace = json.loads(json.dumps(TRACE))
        self.fixed = json.loads(json.dumps(FIXED_CLIPS))
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.hide: set[str] = set()          # GET paths to answer 404 ("not available yet")
        self.clients: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._make_handler())
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def __enter__(self) -> "StandIn":
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._httpd.shutdown()

    # ---------------------------------------------------------------- test helpers
    def push(self, event: str, data: Any) -> None:
        """Broadcast an SSE event to every connected client."""
        with self._lock:
            for q in list(self.clients):
                q.put((event, data))

    def last(self, path_part: str) -> tuple[str, dict[str, Any]] | None:
        for path, body in reversed(self.requests):
            if path_part in path:
                return (path, body)
        return None

    # ---------------------------------------------------------------- request handler
    def _make_handler(self) -> type:
        standin = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a: Any) -> None:      # silence per-request noise
                pass

            def _json(self, obj: Any, status: int = 200) -> None:
                body = json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self) -> dict[str, Any]:
                n = int(self.headers.get("Content-Length") or 0)
                if not n:
                    return {}
                try:
                    return json.loads(self.rfile.read(n).decode() or "{}")
                except ValueError:
                    return {}

            def do_GET(self) -> None:
                path = self.path.split("?")[0]
                if path.startswith("/face/"):
                    return self._face(path[len("/face/"):])
                if path == "/operator/" or path.startswith("/operator/js/") or path in ("/operator/console.css",):
                    return self._ui(path)
                if path == "/operator/api/state":
                    return self._json(standin.snapshot)
                if path == "/operator/api/health":
                    if "/operator/api/health" in standin.hide:
                        return self._json({"ok": False, "reason": "not_found"}, 404)
                    return self._json({"rows": standin.health_rows})
                if path == "/operator/api/outbox":
                    if "/operator/api/outbox" in standin.hide:
                        return self._json({"ok": False, "reason": "not_found"}, 404)
                    return self._json({"items": standin.outbox})
                if path == "/operator/api/trace":
                    return self._json(standin.trace)
                if path == "/operator/api/fixed":
                    return self._json(standin.fixed)
                if path == "/operator/stream":
                    return self._stream()
                return self._json({"ok": False, "reason": "not_found"}, 404)

            def do_POST(self) -> None:
                path = self.path.split("?")[0]
                body = self._body()
                with standin._lock:
                    standin.requests.append((path, body))
                if path == "/operator/api/override" and body.get("kind") == "knobs":
                    knobs = {**standin.snapshot["knobs"],
                             **{k: v for k, v in body.items() if k in ("flash_thinking", "sonnet_thinking", "interruption_enabled")}}
                    standin.snapshot = {**standin.snapshot, "knobs": knobs}
                    return self._json({"ok": True, "knobs": knobs})
                if path.startswith("/operator/api/outbox/") and path.endswith("/press"):
                    oid = path.split("/")[4]
                    button = body.get("button")
                    for row in standin.outbox:
                        if row["id"] == oid:
                            row["status"] = "acknowledged" if row["channel"] == "twilio" else "pressed"
                            row["pressed"] = button
                            standin.push("outbox", row)
                    return self._json({"ok": True})
                return self._json({"ok": True})

            def _face(self, rest: str) -> None:
                name = rest or "index.html"
                ctype = {"index.html": "text/html", "face.js": "text/javascript",
                         "bridge.js": "text/javascript", "face.css": "text/css"}.get(name, "application/octet-stream")
                try:
                    body = (standin.face_dir / name).read_bytes()
                except OSError:
                    return self._json({"ok": False, "reason": "not_found"}, 404)
                self.send_response(200)
                self.send_header("Content-Type", f"{ctype}; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _ui(self, path: str) -> None:
                """Serve the console's own files (index.html, console.css, js/*)."""
                rel = path[len("/operator/"):] or "index.html"
                ctype = {"html": "text/html", "css": "text/css", "js": "text/javascript",
                         "json": "application/json"}.get(rel.rsplit(".", 1)[-1], "application/octet-stream")
                try:
                    body = (standin.ui_dir / rel).read_bytes()
                except OSError:
                    return self._json({"ok": False, "reason": "not_found"}, 404)
                self.send_response(200)
                self.send_header("Content-Type", f"{ctype}; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _stream(self) -> None:
                q: queue.Queue = queue.Queue()
                with standin._lock:
                    standin.clients.append(q)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(b"retry: 500\n\n")
                    self.wfile.write(f"event: state\ndata: {json.dumps(standin.snapshot, ensure_ascii=False)}\n\n".encode())
                    if "/operator/api/health" not in standin.hide:
                        self.wfile.write(f"event: health\ndata: {json.dumps({'rows': standin.health_rows}, ensure_ascii=False)}\n\n".encode())
                    self.wfile.flush()
                    while True:
                        try:
                            event, data = q.get(timeout=0.25)
                            self.wfile.write(f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode())
                            self.wfile.flush()
                        except queue.Empty:
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    with standin._lock:
                        if q in standin.clients:
                            standin.clients.remove(q)

        return Handler
