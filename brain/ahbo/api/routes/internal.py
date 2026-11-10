"""`/internal/*` (CONTRACTS §5): agent → api over the Docker network, `Authorization: Bearer <INTERNAL_TOKEN>`.
Caddy never proxies these. Thin wrappers around `AgentBridge`; routes the agent component provides itself are
registered first and win (these are the fallback)."""
from __future__ import annotations

import inspect
from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from ahbo.core.events import Event
from ahbo.core.ids import new_id

from .. import demo
from ..auth import require_internal
from ..services import _import_optional

router = APIRouter(prefix="/internal", dependencies=[Depends(require_internal)])
ALLOWED_EVENTS = ("session.closed", "agent.state")


def _bridge(request: Request):
    return request.app.state.svc.bridge


@router.post("/turn")
async def turn(request: Request, body: dict[str, Any] = Body(...)) -> Any:
    b = _bridge(request)
    op = body.get("op")
    sid, tid = body.get("session_id"), body.get("turn_id") or new_id("turn")
    if op == "user":
        return await b.user_turn(sid, tid, str(body.get("text") or ""), body.get("stt_confidence"),
                                 bool(body.get("final", True)))
    if op == "reply_start":
        await b.reply_start(sid, tid, str(body.get("text") or ""), body.get("tone"))
        return {"ok": True}
    if op == "reply_end":
        return await b.reply_end(sid, tid)
    return JSONResponse({"ok": False, "reason": "bad_op"}, status_code=400)


@router.post("/ui")
async def ui(request: Request, body: dict[str, Any] = Body(...)) -> Any:
    b = _bridge(request)
    op = body.get("op")
    if op == "steps_plan":
        return await b.steps_plan(body.get("session_id"), body.get("turn_id"), list(body.get("tools") or []))
    if op == "agent_state":
        return await b.agent_state(body.get("session_id"), str(body.get("state") or "idle"),
                                   bool(body.get("user_speaking")))
    return JSONResponse({"ok": False, "reason": "bad_op"}, status_code=400)


@router.post("/tool/{name}")
async def tool(request: Request, name: str, body: dict[str, Any] = Body(...)) -> Any:
    return await _bridge(request).call_tool(body.get("session_id"), body.get("turn_id"), name, body.get("args") or {},
                                            body.get("call_id"))


@router.post("/event")
async def event(request: Request, body: dict[str, Any] = Body(...)) -> Any:
    t = str(body.get("type") or "")
    if t not in ALLOWED_EVENTS and not t.startswith("quality."):
        return JSONResponse({"ok": False, "reason": "type_not_allowed"}, status_code=400)
    p = body.get("payload") or {}
    await request.app.state.svc.kernel.process(Event(type=t, source="agent", correlation_id=p.get("session_id"),
                                                     payload=p))
    return {"ok": True}


async def _delegate(request: Request, names: tuple[str, ...], *args: Any) -> Any:
    for modname in ("ahbo.agent.wiring", "ahbo.agent.context", "ahbo.agent.pull"):
        mod = _import_optional(modname)
        for n in names:
            fn = getattr(mod, n, None) if mod else None
            if callable(fn):
                r = fn(request.app.state.svc, *args)
                return await r if inspect.isawaitable(r) else r
    return JSONResponse({"ok": False, "reason": "not_available"}, status_code=501)


@router.get("/context/{session_id}")
async def context(request: Request, session_id: str) -> Any:
    return await _delegate(request, ("internal_context", "context_for"), session_id)


@router.post("/session/claim")
async def claim(request: Request, body: dict[str, Any] = Body(...)) -> Any:
    return await _delegate(request, ("internal_claim", "claim_session"), body)


@router.get("/pull/{session_id}")
async def pull(request: Request, session_id: str) -> Any:
    return await _delegate(request, ("internal_pull", "pull_items"), session_id)


@router.post("/clock")
async def clock(request: Request, body: dict[str, Any] = Body(...)) -> Any:
    """Used by `ahbo clock` inside the api container."""
    try:
        return await demo.set_clock(request.app.state.svc, body)
    except (ValueError, TypeError):
        return JSONResponse({"ok": False, "reason": "bad_args"}, status_code=400)


@router.post("/reset")
async def reset(request: Request) -> Any:
    """Used by `make demo-fake`: the console's Reset demo."""
    return await demo.reset_demo(request.app.state.svc)
