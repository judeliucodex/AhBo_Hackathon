"""Device Hub (§10.1, CONTRACTS §2): the always-open WebSocket to the iPad (and browser iPads).

- Auth: `Authorization: Bearer <APP_SECRET>`, or the operator cookie with `?sim=1`; anything else closes 4401.
- `hello` → `welcome {device_token, server_time, fixed_cache_version, text_sessions, config}`, then the face
  state is replayed (`kernel.ui.replay_commands()`), also on `face_event {event:{type:"ready"}}`.
- Kernel `device.*` commands become device frames, broadcast to every ready device, each through an ordered
  per-connection send queue. A step `done`/`failed` is held until that step has been `active` ≥ 600 ms (§23.6);
  everything behind it waits (order is never broken). A P0 command drops the queued non-P0 `ui` frames.
- Every command needs an `ack` ≤ 2 s; the first ack counts (latency recorded). P0 frames without an ack are
  resent (same id) up to 3× at 2 s; then the device shows red on the console and the Kernel carries on.
- Device events become Kernel events; `playback_done` is de-duplicated by `say_id`; text-session `utterance`
  frames go to the text loop. Devices are tracked in the `devices` table; `device.offline` after 30 s away.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from ahbo.core.events import Command, Event
from ahbo.core.ids import ulid
from ahbo.core.jsonutil import dumps

log = logging.getLogger("ahbo.device_hub")

ACK_TIMEOUT_S = 2.0
P0_RESENDS = 3
STEP_MIN_ACTIVE_S = 0.6
OFFLINE_AFTER_S = 30.0
SAY_DEDUP = 512

# Kernel command type → device frame type (CONTRACTS §2.2)
FRAME_TYPE = {
    "device.ui": "ui",
    "device.play_fixed": "play_fixed",
    "device.play_voice_message": "play_voice_message",
    "device.capture_photo": "capture_photo",
    "device.session_open": "session.open",
    "device.session_close": "session.close",
    "device.wake_enable": "wake.enable",
    "device.config_update": "config.update",
    "device.stop_all": "stop_all",
}
FRAME_FIELDS = {
    "play_fixed": ("clip_id", "say_id", "mood"),
    "play_voice_message": ("message_id", "url", "say_id", "from_name", "text", "duration_s"),
    "capture_photo": ("incident_id",),
    "session.open": ("reason", "mode", "session_id", "text_only"),
    "session.close": ("session_id",),
    "wake.enable": ("on",),
    "config.update": ("quiet_hours", "interruption_enabled", "wake", "fixed_cache_version", "reminder_mirror"),
    "stop_all": (),
}


def command_to_frame(c: Command) -> dict[str, Any] | None:
    ftype = FRAME_TYPE.get(c.type)
    if ftype is None:
        return None
    fid = c.id.split("_", 1)[1] if "_" in c.id else c.id      # device frames carry a bare ULID
    frame: dict[str, Any] = {"id": fid, "type": ftype, "priority": c.priority}
    if ftype == "ui":
        frame["ui"] = c.payload.get("ui") or {}
    else:
        for k in FRAME_FIELDS[ftype]:
            if c.payload.get(k) is not None:
                frame[k] = c.payload[k]
    return frame


def _step_keys(ui: dict[str, Any]) -> tuple[list[tuple[str, str]], tuple[str, str] | None]:
    """(steps that become active with this face command, the step this command finishes or None)."""
    t = ui.get("type")
    if t == "step":
        key = (str(ui.get("group")), str(ui.get("id")))
        if ui.get("state") == "active":
            return [key], None
        if ui.get("state") in ("done", "failed"):
            return [], key
    elif t == "steps":
        g = ui.get("group") or {}
        return [(str(g.get("id")), str(s.get("id"))) for s in g.get("steps", []) if s.get("state") == "active"], None
    return [], None


@dataclass
class Outgoing:
    frame: dict[str, Any]
    priority: int
    is_ui: bool


@dataclass
class Conn:
    ws: WebSocket
    sim: bool
    device_id: str | None = None
    ready: bool = False
    queue: deque[Outgoing] = field(default_factory=deque)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    step_active: dict[tuple[str, str], float] = field(default_factory=dict)
    sender: asyncio.Task | None = None
    closed: bool = False

    def enqueue(self, item: Outgoing) -> None:
        if item.priority == 0:
            # A P0 command makes queued non-P0 face commands stale (the fall flow starts with `reset`).
            self.queue = deque(x for x in self.queue if not (x.is_ui and x.priority > 0))
        self.queue.append(item)
        self.changed.set()


@dataclass
class Pending:
    cmd_id: str
    frame: dict[str, Any]
    priority: int
    first_sent: float
    last_sent: float
    resends: int = 0
    acked: bool = False
    failed: bool = False


class DeviceHub:
    def __init__(self, svc: Any):
        self.svc = svc
        self.kernel = svc.kernel
        self.db = svc.db
        self.settings = svc.settings
        self.conns: list[Conn] = []
        self.pending: OrderedDict[str, Pending] = OrderedDict()
        self.say_seen: OrderedDict[str, None] = OrderedDict()
        self.info: dict[str, dict[str, Any]] = {}       # device_id -> live info (latency, unacked P0, …)
        self._offline_timers: dict[str, asyncio.TimerHandle] = {}
        self._ticker: asyncio.Task | None = None
        self.kernel.on("device", self.dispatch)

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._ticker is None:
            self._ticker = asyncio.get_running_loop().create_task(self._tick(), name="device_hub_tick")
        self.db.execute("UPDATE devices SET online=0")

    async def stop(self) -> None:
        if self._ticker:
            self._ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._ticker
            self._ticker = None
        for h in self._offline_timers.values():
            h.cancel()
        for c in list(self.conns):
            await self._close(c, 1001)

    # ------------------------------------------------------------------ Kernel → devices
    def dispatch(self, c: Command) -> None:
        """Kernel dispatcher for every `device.*` command. Never blocks (called under the Kernel lock)."""
        frame = command_to_frame(c)
        if frame is None:
            return
        ready = [x for x in self.conns if x.ready and not x.closed]
        if not ready:
            self.kernel.mark_command(c.id, "dropped", "no_device")
            return
        now = time.monotonic()
        self.pending[frame["id"]] = Pending(cmd_id=c.id, frame=frame, priority=c.priority, first_sent=now,
                                            last_sent=now)
        while len(self.pending) > 2000:
            self.pending.popitem(last=False)
        item = Outgoing(frame=frame, priority=c.priority, is_ui=frame["type"] == "ui")
        for conn in ready:
            conn.enqueue(item)
        self.kernel.mark_command(c.id, "sent")

    def reset_queues(self) -> None:
        """Reset demo / panic: drop everything not yet sent and forget pending acks."""
        for conn in self.conns:
            conn.queue.clear()
            conn.step_active.clear()
            conn.changed.set()
        self.pending.clear()
        for info in self.info.values():
            info["unacked_p0"] = 0

    async def send_ui_direct(self, conn: Conn, cmds: list[dict[str, Any]]) -> None:
        """Replay: face commands to one connection, in order, through its queue (P1)."""
        for ui in cmds:
            frame = {"id": ulid(), "type": "ui", "priority": 1, "ui": ui}
            conn.enqueue(Outgoing(frame=frame, priority=1, is_ui=True))

    async def _sender(self, conn: Conn) -> None:
        try:
            while not conn.closed:
                if not conn.queue:
                    conn.changed.clear()
                    await conn.changed.wait()
                    continue
                item = conn.queue[0]
                if item.is_ui:
                    _, finishing = _step_keys(item.frame["ui"])
                    if finishing is not None and finishing in conn.step_active:
                        wait = STEP_MIN_ACTIVE_S - (time.monotonic() - conn.step_active[finishing])
                        if wait > 0:
                            conn.changed.clear()
                            with contextlib.suppress(asyncio.TimeoutError):
                                await asyncio.wait_for(conn.changed.wait(), wait)
                            continue          # re-evaluate the head (a P0 may have dropped it)
                if not conn.queue or conn.queue[0] is not item:
                    continue
                conn.queue.popleft()
                if item.is_ui:
                    started, finishing = _step_keys(item.frame["ui"])
                    for k in started:
                        conn.step_active[k] = time.monotonic()
                    if finishing is not None:
                        conn.step_active.pop(finishing, None)
                    if item.frame["ui"].get("type") in ("reset", "steps_clear"):
                        conn.step_active.clear()
                await self._send(conn, item.frame)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("device sender failed")

    async def _send(self, conn: Conn, frame: dict[str, Any]) -> bool:
        if conn.closed or conn.ws.application_state != WebSocketState.CONNECTED:
            return False
        try:
            await conn.ws.send_text(dumps(frame))
            return True
        except Exception:
            conn.closed = True
            return False

    async def _tick(self) -> None:
        """Ack timeouts: P0 resends (≤ 3 at 2 s), then mark failed and the device red."""
        while True:
            await asyncio.sleep(0.25)
            try:
                now = time.monotonic()
                for fid, p in list(self.pending.items()):
                    if p.acked or p.failed:
                        if now - p.first_sent > 60:
                            self.pending.pop(fid, None)
                        continue
                    if now - p.last_sent < ACK_TIMEOUT_S:
                        continue
                    if p.priority == 0 and p.resends < P0_RESENDS:
                        p.resends += 1
                        p.last_sent = now
                        for conn in self.conns:
                            if conn.ready and not conn.closed:
                                conn.enqueue(Outgoing(frame=p.frame, priority=0, is_ui=False))
                        continue
                    p.failed = True
                    self.kernel.mark_command(p.cmd_id, "failed" if p.priority == 0 else "sent", "no_ack")
                    if p.priority == 0:
                        for conn in self.conns:
                            if conn.device_id:
                                info = self.info.setdefault(conn.device_id, {})
                                info["unacked_p0"] = info.get("unacked_p0", 0) + 1
                        log.warning("P0 command %s unacked after %d resends", p.cmd_id, P0_RESENDS)
            except Exception:
                log.exception("device hub tick failed")

    # ------------------------------------------------------------------ connections
    def authorize(self, ws: WebSocket) -> bool | None:
        """True = iPad (APP_SECRET), False = browser iPad (cookie + ?sim=1), None = refused."""
        from ahbo.api.auth import bearer, check_app_secret, has_operator_cookie
        if check_app_secret(self.settings, bearer(ws)):
            return True
        if ws.query_params.get("sim") == "1" and has_operator_cookie(self.settings, ws):
            return False
        return None

    async def handle(self, ws: WebSocket) -> None:
        kind = self.authorize(ws)
        if kind is None:
            # Accept first: a close before accept becomes an HTTP 403 and the client never sees code 4401.
            await ws.accept()
            await ws.close(code=4401)
            return
        await ws.accept()
        conn = Conn(ws=ws, sim=not kind)
        self.conns.append(conn)
        conn.sender = asyncio.get_running_loop().create_task(self._sender(conn), name="device_sender")
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                try:
                    await self._on_frame(conn, msg)
                except Exception:
                    log.exception("device frame %s failed", msg.get("type"))
        except (WebSocketDisconnect, RuntimeError, KeyError):
            pass
        finally:
            await self._close(conn)

    async def _close(self, conn: Conn, code: int = 1000) -> None:
        if conn in self.conns:
            self.conns.remove(conn)
        conn.closed = True
        conn.changed.set()
        if conn.sender:
            conn.sender.cancel()
        with contextlib.suppress(Exception):
            if conn.ws.application_state == WebSocketState.CONNECTED:
                await conn.ws.close(code)
        dev = conn.device_id
        if dev and not any(c.device_id == dev for c in self.conns):
            self.db.execute("UPDATE devices SET online=0, last_seen=? WHERE id=?", (self.svc.clock.iso(), dev))
            self._schedule_offline(dev)

    def _schedule_offline(self, dev: str) -> None:
        old = self._offline_timers.pop(dev, None)
        if old:
            old.cancel()

        def fire() -> None:
            self._offline_timers.pop(dev, None)
            if not any(c.device_id == dev for c in self.conns):
                self.kernel.submit_nowait(Event(type="device.offline", source="system", payload={"device_id": dev}))

        self._offline_timers[dev] = asyncio.get_running_loop().call_later(OFFLINE_AFTER_S, fire)

    # ------------------------------------------------------------------ devices → Kernel
    async def _on_frame(self, conn: Conn, msg: dict[str, Any]) -> None:
        t = msg.get("type")
        if t == "hello":
            await self._hello(conn, msg)
            return
        if not conn.ready:
            return                                   # nothing before hello
        dev = conn.device_id
        if t == "ack":
            self._ack(conn, msg)
        elif t == "heartbeat":
            self.db.execute("UPDATE devices SET last_seen=?, battery=?, thermal=?, online=1 WHERE id=?",
                            (self.svc.clock.iso(), msg.get("battery"), msg.get("thermal"), dev))
            info = self.info.setdefault(dev, {})
            info["in_session"] = bool(msg.get("in_session"))
            await self.kernel.submit(Event(type="device.heartbeat", source="device", payload={
                "device_id": dev, "battery": msg.get("battery"), "thermal": msg.get("thermal"),
                "in_session": bool(msg.get("in_session"))}))
            # liveness answer (no `id`: not a command, never acked): lets a client notice a silently dead path
            await self._send(conn, {"type": "pong", "ref": msg.get("id")})
        elif t == "wake":
            await self.kernel.process(Event(type="device.wake", source="device", payload={
                "trigger": msg.get("trigger", "touch"), "s1_score": msg.get("s1_score"),
                "s2_text": msg.get("s2_text"), "device_id": dev}))
        elif t == "touch_ok":
            await self.kernel.process(Event(type="device.touch_ok", source="device", payload={"device_id": dev}))
        elif t == "playback_done":
            say_id = msg.get("say_id")
            if say_id:
                if say_id in self.say_seen:
                    return
                self.say_seen[say_id] = None
                while len(self.say_seen) > SAY_DEDUP:
                    self.say_seen.popitem(last=False)
            payload = {"say_id": say_id, "device_id": dev}
            if msg.get("clip_id"):
                payload["clip_id"] = msg["clip_id"]
            mid = msg.get("message_id") or (msg.get("id_") if msg.get("id_") else None)
            if mid:
                payload["message_id"] = mid
            if msg.get("error"):
                payload["error"] = msg["error"]
            await self.kernel.process(Event(type="device.playback_done", source="device", payload=payload))
        elif t == "photo_uploaded":
            await self.kernel.process(Event(type="device.photo_uploaded", source="device",
                                            correlation_id=msg.get("incident_id"), payload={
                                                "photo_id": msg.get("photo_id"),
                                                "incident_id": msg.get("incident_id"), "device_id": dev}))
        elif t == "face_event":
            ev = msg.get("event") if isinstance(msg.get("event"), dict) else {}
            await self.kernel.process(Event(type="device.face_event", source="device",
                                            payload={"event": ev, "device_id": dev}))
            if ev.get("type") == "ready":
                await self.replay(conn)
        elif t == "utterance":
            text = str(msg.get("text") or "").strip()
            if text and msg.get("final", True) and self.svc.text_loop is not None:
                await self.svc.text_loop.utterance(text)
        elif t == "error":
            await self.kernel.submit(Event(type="device.error", source="device", payload={
                "device_id": dev, "code": msg.get("code"), "detail": str(msg.get("detail") or "")[:500]}))

    def _ack(self, conn: Conn, msg: dict[str, Any]) -> None:
        ref = str(msg.get("ref") or "")
        p = self.pending.get(ref)
        if p is None or p.acked:
            return                                   # replay frames / duplicate acks: first ack counts
        p.acked = True
        latency_ms = round((time.monotonic() - p.first_sent) * 1000, 1)
        ok = bool(msg.get("ok", True))
        self.kernel.mark_command(p.cmd_id, "acked" if ok else "failed", None if ok else str(msg.get("error"))[:200])
        info = self.info.setdefault(conn.device_id or "?", {})
        info["ack_latency_ms"] = latency_ms
        if p.failed and p.priority == 0:
            info["unacked_p0"] = max(0, info.get("unacked_p0", 0) - 1)
        self.kernel.submit_nowait(Event(type="device.ack", source="device", payload={
            "ref": p.cmd_id, "ok": ok, "error": msg.get("error"), "latency_ms": latency_ms,
            "device_id": conn.device_id}))

    async def _hello(self, conn: Conn, msg: dict[str, Any]) -> None:
        from ahbo.api.auth import make_device_token
        dev = str(msg.get("device_id") or ("sim-" + ulid()[-6:] if conn.sim else "ipad"))[:64].replace(".", "-")
        conn.device_id = dev
        old = self._offline_timers.pop(dev, None)
        if old:
            old.cancel()
        now = self.svc.clock.iso()
        self.db.execute(
            "INSERT INTO devices(id, last_seen, app_version, fixed_cache_version, wake_engine, online) "
            "VALUES (?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen, "
            "app_version=excluded.app_version, fixed_cache_version=excluded.fixed_cache_version, "
            "wake_engine=excluded.wake_engine, online=1",
            (dev, now, msg.get("app_version"), msg.get("fixed_cache_version"), msg.get("wake_engine")))
        self.info.setdefault(dev, {}).update({"sim": conn.sim, "unacked_p0": 0})
        manifest_version = self.fixed_cache_version()
        welcome = {"id": ulid(), "type": "welcome", "device_token": make_device_token(self.settings, dev),
                   "server_time": now, "fixed_cache_version": manifest_version,
                   "text_sessions": self.settings.is_fake("livekit"), "config": self.device_config()}
        await self._send(conn, welcome)
        conn.ready = True
        await self.kernel.submit(Event(type="device.online", source="device", payload={
            "device_id": dev, "app_version": msg.get("app_version"),
            "fixed_cache_version": msg.get("fixed_cache_version"), "wake_engine": msg.get("wake_engine"),
            "sim": conn.sim}))
        await self.replay(conn)

    async def replay(self, conn: Conn) -> None:
        conn.queue = deque(x for x in conn.queue if not x.is_ui)    # the replay supersedes queued face commands
        conn.step_active.clear()
        await self.send_ui_direct(conn, self.kernel.ui.replay_commands())

    # ------------------------------------------------------------------ config for welcome
    def fixed_cache_version(self) -> str:
        from ahbo.api.services import read_manifest
        return str(read_manifest(self.svc).get("version") or "")

    def device_config(self) -> dict[str, Any]:
        db = self.db
        res = db.one("SELECT quiet_hours FROM resident LIMIT 1")
        quiet = json.loads(res["quiet_hours"]) if res and res.get("quiet_hours") else {"start": "22:30", "end": "07:00"}
        knobs = db.kv_get("knobs", {}) or {}
        wake_keys = ("require_wai", "require_two_updates", "utterance_start_silence_ms", "cooldown_after_reject_s",
                     "allow_missing_aa", "plain_abou_wakes")
        wm = self.svc.config.wake_match or {}
        return {"quiet_hours": quiet, "interruption_enabled": bool(knobs.get("interruption_enabled", True)),
                "wake": {k: wm[k] for k in wake_keys if k in wm}, "reminder_mirror": self.reminder_mirror()}

    def reminder_mirror(self) -> dict[str, Any]:
        db = self.db
        cat = {r["id"]: r for r in db.query("SELECT * FROM med_catalogue")}
        slots = []
        for s in db.query("SELECT * FROM med_schedule WHERE active=1 ORDER BY time"):
            cards = []
            for mid in json.loads(s["med_names"] or "[]"):
                m = cat.get(mid)
                if not m:
                    continue
                small = " ".join(x for x in (m.get("en"), m.get("dose")) if x)
                cards.append({"id": f"med_{mid}", "kind": "med", "label": m["yue"], "small": small,
                              "pill": json.loads(m.get("pill") or "[]")})
            slots.append({"slot_id": s["slot_id"], "time": s["time"], "clip_id": f"R_{s['slot_id']}", "cards": cards})
        return {"date": self.svc.clock.today().isoformat(), "slots": slots}

    # ------------------------------------------------------------------ console snapshot
    def snapshot(self) -> list[dict[str, Any]]:
        out = []
        connected = {c.device_id for c in self.conns if c.ready and not c.closed}
        for r in self.db.query("SELECT * FROM devices ORDER BY last_seen DESC LIMIT 20"):
            info = self.info.get(r["id"], {})
            out.append({"device_id": r["id"], "online": r["id"] in connected, "last_seen": r["last_seen"],
                        "battery": r["battery"], "thermal": r["thermal"], "wake_engine": r["wake_engine"],
                        "app_version": r["app_version"], "fixed_cache_version": r["fixed_cache_version"],
                        "ack_latency_ms": info.get("ack_latency_ms"), "unacked_p0": info.get("unacked_p0", 0),
                        "sim": info.get("sim", (r["id"] or "").startswith("sim"))})
        return out

    @property
    def connected_count(self) -> int:
        return sum(1 for c in self.conns if c.ready and not c.closed)
