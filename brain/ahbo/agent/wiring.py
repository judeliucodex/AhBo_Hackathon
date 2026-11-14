"""Agent component wiring (AGENT_BRIEF "wiring convention").

- `build_agent(kernel) -> (bridge, text_loop)`: the AgentBridge + the in-process text loop with the configured Flash
  (FakeFlash in fake mode). Called by `ahbo.api.services` before the components are installed.
- `install(svc)`: Kernel dispatchers
    `vision.analyze` → Vision adapter (advisory, 8 s) → `vision.result {incident_id, note}` event
    `deep.start`     → Deep runner (FakeDeep / Sonnet loop, ≤ 90 s) → `deep.result {task_id, result}` (validated §7)
    `agent.inject | agent.mode | agent.pause | agent.resume`, `session.open | session.close`
                     → the text loop for text sessions (fake LiveKit), else the per-session pull queue that the
                       LiveKit worker long-polls (`GET /internal/pull/{session_id}`)
  plus the model runtime (knobs, costs, spans).
- `/internal/*` hooks, looked up by name by `ahbo.api.routes.internal._delegate(svc, …)`:
    `internal_context(svc, session_id)`, `internal_claim(svc, body)`, `internal_pull(svc, session_id)`.
- `warmup(svc)`: optional pre-demo warm-up (prompts, weather cache, one tiny Flash call when real).
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from ahbo.core.events import Command, Event

from . import prompts

log = logging.getLogger("ahbo.agent.wiring")

router = None
PULL_WAIT_S = 25.0
DEEP_HARD_LIMIT_S = 100.0        # the loop stops itself at 90 s; this only guards a stuck adapter
INTERRUPTING_MODES = ("paused", "kernel_listen")


# ====================================================================== build
def build_agent(kernel: Any) -> tuple[Any, Any]:
    from ahbo.models import get_flash

    from .bridge import AgentBridge
    from .runner import TurnAgent
    from .text_loop import TextLoop
    bridge = AgentBridge(kernel)
    loop = TextLoop(bridge, TurnAgent(bridge, get_flash()))
    return bridge, loop


# ====================================================================== pull queues (LiveKit worker)
class PullQueues:
    """Kernel `agent.*` commands for a LiveKit session, delivered by long-poll (CONTRACTS §5 `/internal/pull`)."""

    def __init__(self) -> None:
        self.queues: dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)
        self.wake: dict[str, dict[str, Any]] = {}          # session_id → {trigger, s2_text} when opened by wake

    def put(self, session_id: str | None, item: dict[str, Any]) -> None:
        if session_id:
            self.queues[session_id].put_nowait(item)

    def peek(self, session_id: str) -> list[dict[str, Any]]:
        q = self.queues.get(session_id)
        return [dict(x) for x in list(q._queue)] if q is not None else []      # noqa: SLF001 (read-only peek)

    async def pull(self, session_id: str, wait_s: float = PULL_WAIT_S) -> list[dict[str, Any]]:
        q = self.queues[session_id]
        items: list[dict[str, Any]] = []
        try:
            items.append(await asyncio.wait_for(q.get(), wait_s))
        except asyncio.TimeoutError:
            return []
        while not q.empty():
            items.append(q.get_nowait())
        return items

    def drop(self, session_id: str) -> None:
        self.queues.pop(session_id, None)
        self.wake.pop(session_id, None)


def _text_mode(svc: Any) -> bool:
    s = svc.kernel.state.session
    if s is not None:
        return bool(s.text_only)
    return svc.settings.is_fake("livekit")


def _session_id(svc: Any, c: Command) -> str | None:
    sid = c.payload.get("session_id")
    s = svc.kernel.state.session
    return sid or (s.id if s else None)


# ====================================================================== install
def install(svc: Any) -> None:
    from ahbo.models import runtime
    runtime.set_runtime(svc.db, svc.clock)
    k = svc.kernel
    pq = _PQ[id(svc)] = PullQueues()
    loop = svc.text_loop
    last_wake: dict[str, Any] = {}

    def on_event(kind: str, obj: dict[str, Any]) -> None:
        if kind == "kernel_event" and obj.get("type") == "device.wake":
            last_wake.clear()
            last_wake.update(obj.get("payload") or {})
        elif kind == "kernel_event" and obj.get("type") == "operator.model_config":
            # the 🚫 no-interrupt toggle: apply to the RUNNING session, not just the next one
            if "interruption_enabled" in (obj.get("payload") or {}):
                s = svc.kernel.state.session
                if s is not None:
                    pq.put(s.id, {"kind": "config",
                                  "interruption_enabled": bool(obj["payload"]["interruption_enabled"])})

    k.subscribe(on_event)

    # ---- vision.analyze → vision.result
    def on_vision(c: Command) -> None:
        svc.spawn(analyze_photo(svc, c), name="vision")

    # ---- deep.start → deep.result
    def on_deep(c: Command) -> None:
        svc.spawn(run_deep(svc, c), name="deep")

    # ---- agent.* and session.* → text loop or pull queue
    def on_inject(c: Command) -> None:
        if _text_mode(svc):
            if loop is not None:
                loop._on_inject(c)
            return
        p = c.payload
        pq.put(_session_id(svc, c), {"kind": "inject", "priority": p.get("priority", c.priority),
                                     "instructions": prompts.system_line(p.get("kind", "提醒"), p.get("content", ""),
                                                                         p.get("instruction", ""))})

    def on_mode(c: Command) -> None:
        mode = c.payload.get("mode") or "companion"
        if _text_mode(svc):
            if mode in INTERRUPTING_MODES and loop is not None:
                _interrupt_text(loop)
            return
        sid = _session_id(svc, c)
        if mode in INTERRUPTING_MODES:
            pq.put(sid, {"kind": "interrupt"})
        pq.put(sid, {"kind": "mode", "mode": mode})

    def on_pause(c: Command) -> None:
        if _text_mode(svc):
            if loop is not None:
                loop._on_pause(c)
            return
        sid = _session_id(svc, c)
        pq.put(sid, {"kind": "interrupt"})
        pq.put(sid, {"kind": "mode", "mode": c.payload.get("mode") or "paused", "hard": bool(c.payload.get("hard"))})

    def on_resume(c: Command) -> None:
        if _text_mode(svc):
            if loop is not None:
                loop._on_resume(c)
            return
        s = k.state.session
        pq.put(_session_id(svc, c), {"kind": "mode", "mode": c.payload.get("mode") or (s.mode if s else "companion")})

    def on_open(c: Command) -> None:
        sid = c.payload.get("session_id")
        if sid and c.payload.get("reason") in ("wakeword", "wake") and last_wake.get("s2_text") is not None:
            pq.wake[sid] = {"trigger": "wakeword", "s2_text": last_wake.get("s2_text")}

    def on_close(c: Command) -> None:
        sid = c.payload.get("session_id")
        if loop is not None:
            loop._on_close(c)
        if not _text_mode(svc) or sid in pq.queues:
            pq.put(sid, {"kind": "close"})
            asyncio.get_running_loop().call_later(30.0, pq.drop, sid)

    k.on("vision.analyze", on_vision)
    k.on("deep.start", on_deep)
    k.on("agent.inject", on_inject)
    k.on("agent.mode", on_mode)
    k.on("agent.pause", on_pause)
    k.on("agent.resume", on_resume)
    k.on("session.open", on_open)
    k.on("session.close", on_close)


_PQ: dict[int, PullQueues] = {}


def _pq(svc: Any) -> PullQueues:
    pq = _PQ.get(id(svc))
    if pq is None:
        pq = _PQ[id(svc)] = PullQueues()
    return pq


def _interrupt_text(loop: Any) -> None:
    """A mode change to paused/kernel_listen interrupts current speech (§5.1)."""
    t = getattr(loop, "task", None)
    if t is not None and not t.done():
        t.cancel()


# ====================================================================== adapters run in the background
async def analyze_photo(svc: Any, c: Command) -> None:
    from ahbo.models import get_vision
    p = c.payload
    incident_id, photo_id = p.get("incident_id"), p.get("photo_id")
    note: str | None = None
    try:
        vision = get_vision(time_scale=svc.kernel.time_scale)
        path = _photo_path(svc, photo_id)
        timeout = float((svc.config.models.get("vision") or {}).get("timeout_s", 8)) * max(svc.kernel.time_scale, 0.05)
        if path is not None or vision.__class__.__name__ == "FakeVision":
            note = await asyncio.wait_for(vision.describe(path or Path("/nonexistent"), correlation_id=incident_id),
                                          max(timeout, 2.0))
    except Exception as e:
        log.warning("vision failed (%s)", type(e).__name__)
    await svc.kernel.submit(Event(type="vision.result", source="system", correlation_id=c.correlation_id or incident_id,
                                  payload={"incident_id": incident_id, "photo_id": photo_id, "note": note,
                                           "ok": note is not None, "advisory": True}))


def _photo_path(svc: Any, photo_id: str | None) -> Path | None:
    if not photo_id:
        return None
    row = svc.db.one("SELECT path FROM media WHERE id=?", (photo_id,))
    if not row:
        return None
    p = Path(row["path"])
    if not p.is_absolute():
        p = svc.settings.data_dir / p
    return p if p.exists() else None


def deep_context(svc: Any) -> dict[str, Any]:
    """Read-only context for the brief: date/time, profile, the next week's calendar."""
    from ahbo.deep import tools as dtools
    ctx = dtools.DeepCtx(svc.db, svc.clock, svc.settings)
    prof = svc.db.one("SELECT name_zh, age, district FROM resident LIMIT 1") or {}
    facts = [f["fact"] for f in svc.db.query("SELECT fact FROM facts WHERE status='active' AND category!='health' "
                                              "ORDER BY importance DESC LIMIT 12")]
    cal = svc.db.query("SELECT datetime, title, place, kind FROM calendar_events WHERE status!='cancelled' "
                       "AND datetime>=? ORDER BY datetime LIMIT 20", (svc.clock.now().date().isoformat(),))
    meds = [r["time"] for r in svc.db.query("SELECT time FROM med_schedule WHERE active=1 ORDER BY time")]
    return {"now": svc.clock.iso(), "profile": {**prof, "facts": facts},
            "calendar": {"events": cal, "medicine_times_fixed": meds}, "_ctx": ctx}


async def run_deep(svc: Any, c: Command) -> None:
    from ahbo.deep import schema
    from ahbo.models import get_deep
    p = c.payload
    task_id = p.get("task_id") or c.id
    result: dict[str, Any]
    try:
        if not _cap_ok(svc):
            result = schema.failed(task_id)
        else:
            ctx = deep_context(svc)
            runner = get_deep(ctx=ctx.pop("_ctx"), time_scale=svc.kernel.time_scale)
            raw = await asyncio.wait_for(runner.run(task_id, str(p.get("goal") or ""), str(p.get("constraints") or ""),
                                                    ctx), DEEP_HARD_LIMIT_S)
            result = schema.finalize(raw, task_id)
    except asyncio.TimeoutError:
        result = schema.failed(task_id, "timeout")
    except Exception:
        log.exception("deep task %s failed", task_id)
        result = schema.failed(task_id)
    await svc.kernel.submit(Event(type="deep.result", source="deep", correlation_id=c.correlation_id or task_id,
                                  payload={"task_id": task_id, "session_id": p.get("session_id"), "result": result}))


def _cap_ok(svc: Any) -> bool:
    if svc.settings.is_fake("models"):
        return True
    try:
        from ahbo.ops.caps import check_cap
        return bool(check_cap(svc.db, svc.clock, svc.config, "deep_tasks")[0]) and \
            bool(check_cap(svc.db, svc.clock, svc.config, "llm_usd")[0])
    except ImportError:
        return True
    except Exception:
        log.exception("cap check failed; allowing the task")
        return True


# ====================================================================== /internal/* hooks
def internal_context(svc: Any, session_id: str) -> dict[str, Any]:
    from .context import context_payload
    out = context_payload(svc.kernel, session_id)
    out["pending_injections"] = [x for x in _pq(svc).peek(session_id) if x.get("kind") == "inject"]
    out["session_id"] = session_id
    return out


def internal_claim(svc: Any, body: dict[str, Any]) -> dict[str, Any]:
    room = str(body.get("room") or "")
    sid = room.removeprefix("ahbo-") if room.startswith("ahbo-") else str(body.get("session_id") or "")
    s = svc.kernel.state.session
    if s is None or s.id != sid:
        return {"ok": False, "reason": "unknown_session"}
    out: dict[str, Any] = {"ok": True, "session_id": s.id, "reason": s.reason, "mode": s.mode,
                           "interruption_enabled": bool((svc.db.kv_get("knobs", {}) or {}).get("interruption_enabled",
                                                                                              False))}   # default: NO interruption
    wake = _pq(svc).wake.get(s.id)
    if wake:
        out["wake"] = wake
    return out


async def internal_pull(svc: Any, session_id: str) -> dict[str, Any]:
    s = svc.kernel.state.session
    pq = _pq(svc)
    if (s is None or s.id != session_id) and session_id not in pq.queues:
        return {"items": [{"kind": "close"}]}
    return {"items": await pq.pull(session_id)}


# ====================================================================== warm-up
async def warmup(svc: Any) -> dict[str, Any]:
    """Pre-demo: load prompts and tools, prime the weather cache, one tiny Flash call when models are real."""
    from .context import build_instructions
    from .tools import load_tools, weather
    load_tools()
    prompts.persona(), prompts.rules(), prompts.deep_prompt()
    out: dict[str, Any] = {"ok": True, "context_chars": len(build_instructions(svc.kernel, None))}
    try:
        await weather.current(svc.settings)
    except Exception:
        pass
    if not svc.settings.is_fake("models"):
        try:
            from ahbo.models import get_flash
            r = await asyncio.wait_for(get_flash().chat([{"role": "user", "content": "喂"}], None, purpose="intent_tag"),
                                       10)
            out["flash"] = bool(r.text or r.tool_calls)
        except Exception as e:
            out["flash"] = f"failed: {type(e).__name__}"
    return out
