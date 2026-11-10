"""Operator API (CONTRACTS §9) and the SSE stream `GET /operator/stream`. Cookie auth on every route."""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ahbo.core.events import Event
from ahbo.core.jsonutil import dumps, loads

from .. import demo
from ..auth import require_operator
from ..services import Services, _import_optional, read_manifest
from ..snapshot import basic_health, state_snapshot

log = logging.getLogger("ahbo.api.operator")
router = APIRouter(dependencies=[Depends(require_operator)])

STATE_MIN_INTERVAL_S = 0.5     # `state` at most 2/s
HEALTH_INTERVAL_S = 30.0
KEEPALIVE_S = 15.0
TURN_EVENTS = {"user.utterance": "user", "agent.reply_start": "ahbo"}


def _svc(request: Request) -> Services:
    return request.app.state.svc


async def _maybe(r: Any) -> Any:
    return await r if inspect.isawaitable(r) else r


# ---------------------------------------------------------------- snapshot / stream
@router.get("/operator/api/state")
async def get_state(request: Request) -> dict:
    return state_snapshot(_svc(request))


def _sse(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {dumps(data)}\n\n".encode()


async def health_rows(svc: Services) -> list[dict[str, Any]]:
    doctor = _import_optional("ahbo.ops.doctor")
    if doctor is not None and hasattr(doctor, "rows"):
        try:
            return await _maybe(doctor.rows(svc))
        except Exception as e:
            log.exception("doctor.rows failed")
            return basic_health(svc) + [{"item": "doctor", "status": "red", "detail": repr(e)[:120]}]
    return basic_health(svc)


@router.get("/operator/stream")
async def stream(request: Request) -> StreamingResponse:
    svc = _svc(request)
    q: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=2000)
    dirty = asyncio.Event()

    def listener(kind: str, obj: dict[str, Any]) -> None:
        try:
            q.put_nowait((kind, obj))
            if kind == "kernel_event" and obj.get("type") in TURN_EVENTS:
                p = obj.get("payload") or {}
                q.put_nowait(("turn", {"role": TURN_EVENTS[obj["type"]], "text": p.get("text"),
                                       "session_id": p.get("session_id"), "turn_id": p.get("turn_id"),
                                       "stt_confidence": p.get("stt_confidence"), "ts": obj.get("ts")}))
        except asyncio.QueueFull:
            pass
        if kind in ("kernel_event", "command"):
            dirty.set()

    unsub = svc.kernel.subscribe(listener)

    async def gen():
        loop = asyncio.get_running_loop()
        last_state = 0.0
        last_health = 0.0
        last_ping = loop.time()
        try:
            yield b"retry: 2000\n\n"
            yield _sse("state", state_snapshot(svc))
            yield _sse("health", {"rows": await health_rows(svc)})
            last_health = last_state = loop.time()
            while True:
                if await request.is_disconnected():
                    break
                try:
                    kind, obj = await asyncio.wait_for(q.get(), 0.25)
                    yield _sse(kind, obj)
                    while not q.empty():
                        kind, obj = q.get_nowait()
                        yield _sse(kind, obj)
                except TimeoutError:
                    pass
                now = loop.time()
                if dirty.is_set() and now - last_state >= STATE_MIN_INTERVAL_S:
                    dirty.clear()
                    last_state = now
                    yield _sse("state", state_snapshot(svc))
                if now - last_health >= HEALTH_INTERVAL_S:
                    last_health = now
                    yield _sse("health", {"rows": await health_rows(svc)})
                if now - last_ping >= KEEPALIVE_S:
                    last_ping = now
                    yield b": ping\n\n"
        finally:
            unsub()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- triggers / overrides / clock / scenarios
def _result(r: dict[str, Any]) -> JSONResponse:
    return JSONResponse(r, status_code=200 if r.get("ok") else 400)


@router.post("/operator/api/trigger")
async def post_trigger(request: Request, body: dict[str, Any] = Body(...)) -> JSONResponse:
    return _result(await demo.trigger(_svc(request), body))


@router.post("/operator/api/override")
async def post_override(request: Request, body: dict[str, Any] = Body(...)) -> JSONResponse:
    return _result(await demo.override(_svc(request), body))


@router.post("/operator/api/clock")
async def post_clock(request: Request, body: dict[str, Any] = Body(...)) -> JSONResponse:
    try:
        return _result(await demo.set_clock(_svc(request), body))
    except (ValueError, TypeError):
        return _result({"ok": False, "reason": "bad_args"})


@router.post("/operator/api/reset")
async def post_reset(request: Request) -> JSONResponse:
    return _result(await demo.reset_demo(_svc(request)))


@router.post("/operator/api/panic")
async def post_panic(request: Request) -> JSONResponse:
    return _result(await demo.panic(_svc(request)))


@router.post("/operator/api/scenario/{name}")
async def post_scenario(request: Request, name: str) -> JSONResponse:
    return _result(demo.start_scenario(_svc(request), name))


# ---------------------------------------------------------------- outbox
def _outbox_mod():
    return _import_optional("ahbo.links.outbox")


@router.get("/operator/api/outbox")
async def get_outbox(request: Request, limit: int = 100) -> dict:
    svc = _svc(request)
    mod = _outbox_mod()
    if mod is not None and hasattr(mod, "outbox_list"):
        return {"items": mod.outbox_list(svc.db, limit=min(max(limit, 1), 500))}
    rows = svc.db.query("SELECT * FROM outbox ORDER BY ts DESC LIMIT ?", (min(max(limit, 1), 500),))
    return {"items": [dict(r, payload=loads(r["payload"], {}), buttons=loads(r["buttons"], []), fake=bool(r["fake"]))
                      for r in rows]}


@router.post("/operator/api/outbox/{oid}/press")
async def press_outbox(request: Request, oid: str, body: dict[str, Any] = Body(...)) -> JSONResponse:
    svc = _svc(request)
    button = str(body.get("button") or "")
    for modname in ("ahbo.links.wiring", "ahbo.links.outbox", "ahbo.links.family", "ahbo.links.phone"):
        mod = _import_optional(modname)
        fn = getattr(mod, "press", None) or getattr(mod, "outbox_press", None) if mod else None
        if callable(fn):
            r = await _maybe(fn(svc, oid, button))
            return _result(r if isinstance(r, dict) else {"ok": bool(r)})
    row = svc.db.one("SELECT * FROM outbox WHERE id=?", (oid,))
    if not row:
        return _result({"ok": False, "reason": "not_found"})
    if row["channel"] == "twilio":
        await svc.kernel.process(Event(type="phone.call_status", source="twilio", correlation_id=row["correlation_id"],
                                       payload={"call_id": row["provider_id"] or oid, "status": "acknowledged",
                                                "incident_id": row["correlation_id"], "outbox_id": oid,
                                                "pressed": button}))
    else:
        await svc.kernel.process(Event(type="family.button", source="telegram", correlation_id=row["correlation_id"],
                                       payload={"contact_id": row["to_contact"], "callback_data": button,
                                                "outbox_id": oid}))
    return _result({"ok": True})


# ---------------------------------------------------------------- trace / health / fixed
@router.get("/operator/api/trace")
async def get_trace(request: Request, correlation_id: str | None = None, limit: int = 200) -> dict:
    db = _svc(request).db
    limit = min(max(limit, 1), 2000)

    def rows(sql_all: str, sql_corr: str) -> list[dict[str, Any]]:
        out = db.query(sql_corr, (correlation_id, limit)) if correlation_id else db.query(sql_all, (limit,))
        for r in out:
            for k in ("payload", "attrs"):
                if k in r:
                    r[k] = loads(r[k], {})
        return list(reversed(out))

    return {
        "events": rows("SELECT * FROM events ORDER BY ts DESC, rowid DESC LIMIT ?",
                       "SELECT * FROM events WHERE correlation_id=? ORDER BY ts DESC, rowid DESC LIMIT ?"),
        "commands": rows("SELECT * FROM commands ORDER BY ts DESC, rowid DESC LIMIT ?",
                         "SELECT * FROM commands WHERE correlation_id=? ORDER BY ts DESC, rowid DESC LIMIT ?"),
        "spans": rows("SELECT * FROM spans ORDER BY start_ts DESC LIMIT ?",
                      "SELECT * FROM spans WHERE correlation_id=? ORDER BY start_ts DESC LIMIT ?"),
    }


@router.get("/operator/api/health")
async def get_health(request: Request) -> dict:
    return {"rows": await health_rows(_svc(request))}


@router.get("/operator/api/fixed")
async def get_fixed(request: Request) -> dict:
    m = read_manifest(_svc(request))
    return {"version": m.get("version"),
            "clips": {cid: {"text": c.get("text"), "duration": c.get("duration")}
                      for cid, c in (m.get("clips") or {}).items()}}

