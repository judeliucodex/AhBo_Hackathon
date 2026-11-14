"""Operator actions (§12, §13, CONTRACTS §9): Reset demo, Panic, virtual clock, scenario runner, triggers and
overrides. Every override is an `operator.*` Kernel event (INV-8); the console never edits the DB directly —
these functions go through the Kernel (Reset demo reloads the seed, the one sanctioned bulk write, §12.1).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ahbo.core.events import P0, P1, Event, cmd, ui
from ahbo.core.ids import new_id
from ahbo.kernel.state import KernelState

from .services import Services

log = logging.getLogger("ahbo.api.demo")

SCENARIOS = ("S1", "S2", "S3", "S4", "S4ok")
S_LINES = {
    "S1": ["阿寶，今日有冇人搵過我呀？"],
    "S2": ["阿寶，我幾時食藥呀？"],
    "S3": ["阿寶，我今日有咩安排呀？", "咁我下晝好悶喎……"],
}
S3_GAP_S = 0.8          # turn 2 is sent this long after turn 1's reply ends
S4OK_DELAY_S = 2.3      # 「我冇事」 this long after the countdown starts
FALL_PAYLOAD = {"peak_g": 3.1, "freefall_ms": 180, "stillness_s": 2.4, "beacon_id": "beacon1", "battery": 0.81}


def op_event(type_: str, **payload: Any) -> Event:
    return Event(type=type_, source="operator", payload=payload)


# ---------------------------------------------------------------- clock
async def set_clock(svc: Services, body: dict[str, Any]) -> dict[str, Any]:
    c = svc.clock
    if body.get("set"):
        c.set_time(str(body["set"]))
    elif "offset_s" in body:
        c.set_offset(float(body["offset_s"]))
    elif "anchor" in body:
        anchors = (svc.seed().get("scenario_anchors") or {})
        name = str(body["anchor"])
        key = "S4" if name == "S4ok" else name
        if key not in anchors:
            return {"ok": False, "reason": "unknown_anchor"}
        c.set_time(anchors[key])
    else:
        return {"ok": False, "reason": "bad_args"}
    svc.save_clock()
    await svc.kernel.process(op_event("operator.clock", **{**body, "offset_s": c.offset_s, "now": c.iso()}))
    return {"ok": True, "now": c.iso(), "offset_s": c.offset_s}


# ---------------------------------------------------------------- reset / panic
async def reset_demo(svc: Services, *, anchor: str | None = None) -> dict[str, Any]:
    """Reload the seed, re-anchor relative times, clear incidents/sessions/cards/queues, face `reset` (≤ 5 s).
    Without an explicit `anchor`, the clock keeps its previous setting — a Reset no longer drags real HKT
    back to the seed's virtual morning (13 Nov: owner wants the clock on real Hong Kong time)."""
    t0 = time.monotonic()
    k = svc.kernel
    try:
        prev_offset = svc.clock.offset_s          # kept when anchor is None (real HKT stays real)
    except Exception:
        prev_offset = None
    if svc.text_loop is not None and getattr(svc.text_loop, "task", None) and not svc.text_loop.task.done():
        svc.text_loop.task.cancel()
    if anchor:
        await set_clock(svc, {"anchor": anchor})
    await k.process(op_event("operator.reset", anchor=anchor))
    async with k._lock:
        for key in list(k._timers):
            k.cancel_timer(key)
        k.state = KernelState()
        while not k.queue.empty():          # drop queued (stale) events; the run loop keeps the same queue
            k.queue.get_nowait()
        from ahbo.ops.seed import load_demo
        load_demo(svc.db, svc.clock, svc.settings)
        if anchor is None and prev_offset is not None:
            svc.clock.set_offset(prev_offset)   # restore the pre-reset clock (real HKT)
        svc.db.kv_set("ui_state", {})
    if svc.hub is not None:
        svc.hub.reset_queues()
    for fn in svc.on_reset:
        try:
            r = fn(svc)
            if asyncio.iscoroutine(r):
                await r
        except Exception:
            log.exception("reset hook failed")
    await k.emit(ui({"type": "reset"}, P0), ui({"type": "mood", "mood": "idle"}, P0),
                 cmd("device.wake_enable", P1, on=True))
    return {"ok": True, "now": svc.clock.iso(), "ms": round((time.monotonic() - t0) * 1000)}


async def panic(svc: Services) -> dict[str, Any]:
    """Esc: stop all audio, close the session, face idle (§12.2)."""
    k = svc.kernel
    await k.process(op_event("operator.panic"))
    if svc.text_loop is not None and getattr(svc.text_loop, "task", None) and not svc.text_loop.task.done():
        svc.text_loop.task.cancel()
    if svc.hub is not None:
        svc.hub.reset_queues()
    await k.emit(cmd("device.stop_all", P0))
    if k.state.session is not None:
        await k.process(Event(type="session.closed", source="operator",
                              payload={"session_id": k.state.session.id, "why": "panic"}))
    await k.emit(ui({"type": "reset"}, P0), ui({"type": "mood", "mood": "idle"}, P0))
    return {"ok": True}


# ---------------------------------------------------------------- scenarios
async def _wait_ui(svc: Services, pred, timeout: float) -> bool:
    fut: asyncio.Future = asyncio.get_running_loop().create_future()

    def listener(kind: str, obj: dict[str, Any]) -> None:
        if kind == "ui" and not fut.done() and pred(obj):
            fut.set_result(True)

    unsub = svc.kernel.subscribe(listener)
    try:
        await asyncio.wait_for(fut, timeout)
        return True
    except TimeoutError:
        return False
    finally:
        unsub()


async def _wait_turn(svc: Services, timeout: float = 60.0) -> None:
    tl = svc.text_loop
    if tl is not None and getattr(tl, "task", None):
        try:
            await asyncio.wait_for(asyncio.shield(tl.task), timeout)
        except (TimeoutError, asyncio.CancelledError, Exception):
            pass


async def run_scenario(svc: Services, name: str, *, scale: float = 1.0) -> None:
    """Drive a scenario's inputs through the real paths (text loop / beacon event)."""
    await reset_demo(svc, anchor=name)
    await asyncio.sleep(0.3 * scale)
    if name in S_LINES:
        lines = S_LINES[name]
        for i, line in enumerate(lines):
            await svc.text_loop.utterance(line)
            await _wait_turn(svc)
            if i < len(lines) - 1:
                await asyncio.sleep(S3_GAP_S * scale)
        return
    from ahbo.models.fakes import FAKE_VISION_FINE, FAKE_VISION_KEY
    svc.db.kv_set(FAKE_VISION_KEY, FAKE_VISION_FINE if name == "S4ok" else "")   # fake camera: S4-OK she's fine
    countdown = asyncio.ensure_future(_wait_ui(svc, lambda u: u.get("type") == "countdown", 60.0)) \
        if name == "S4ok" else None
    await svc.kernel.process(Event(type="sensor.fall_suspected", source="beacon", payload=dict(FALL_PAYLOAD)))
    if countdown is not None and await countdown:
        await asyncio.sleep(S4OK_DELAY_S * scale)
        await svc.text_loop.utterance("我冇事")


def start_scenario(svc: Services, name: str) -> dict[str, Any]:
    if name not in SCENARIOS:
        return {"ok": False, "reason": "unknown_scenario"}
    old = svc.extra.get("scenario_task")
    if old and not old.done():
        old.cancel()
    svc.extra["scenario_task"] = svc.spawn(run_scenario(svc, name), name=f"scenario_{name}")
    return {"ok": True, "scenario": name}


# ---------------------------------------------------------------- triggers (§12.1 panel 3)
def _links():
    """The links component's console fake controls (`ahbo.links.wiring`): `inject_voice_note(svc, file=None)`,
    `inject_text(svc, text)`, `press(svc, outbox_id, button)`; the outbox panel uses `ahbo.links.outbox.outbox_list(db)`
    (both wired in `routes/operator.py`). None when links is not installed (the direct-event fallback is used)."""
    from .services import _import_optional
    mod = _import_optional("ahbo.links.wiring")
    return mod if mod is not None and hasattr(mod, "inject_voice_note") and hasattr(mod, "inject_text") else None


async def trigger(svc: Services, body: dict[str, Any]) -> dict[str, Any]:
    k = svc.kernel
    kind = body.get("kind")
    if kind == "fall":
        await k.process(Event(type="sensor.fall_suspected", source="operator", payload={"by": "operator"}))
    elif kind == "sos":
        await k.process(Event(type="sensor.sos_pressed", source="operator", payload={"by": "operator"}))
    elif kind == "dose_now":
        await k.process(op_event("operator.dose_now", slot_id=body.get("slot_id")))
    elif kind == "checkin":
        await k.process(op_event("operator.checkin_now", checkin=body.get("checkin") or "morning"))
    elif kind == "plan_now":
        await k.process(op_event("operator.plan_now"))
    elif kind == "voice_note":
        links = _links()
        if links is not None:               # fake inbound note from 阿珍, same path as the Telegram webhook
            return await links.inject_voice_note(svc)
        vid = body.get("voice_id") or "vm_demo"
        await k.process(Event(type="family.voice_note", source="operator",
                              payload={"contact_id": body.get("contact_id") or "c_chun", "voice_id": vid}))
    elif kind == "family_text":
        text = str(body.get("text") or "").strip()
        if not text:
            return {"ok": False, "reason": "bad_args"}
        links = _links()
        if links is not None:
            return await links.inject_text(svc, text)
        await k.process(Event(type="family.text", source="operator",
                              payload={"contact_id": body.get("contact_id") or "c_chun", "text": text}))
    elif kind == "reset":
        return await reset_demo(svc)
    elif kind == "family_info":
        # Operator-initiated INFO to the caregiver — same INV-1 path as the tool: the Kernel owns the send,
        # the text is rebuilt from the fixed template + quoted words (her_words), never free-form LLM text.
        words = str(body.get("text") or "").strip()
        if not words:
            return {"ok": False, "reason": "bad_args"}
        await k.process(Event(type="family.message_request", source="operator",
                              payload={"contact_id": body.get("contact_id") or "c_chun", "kind": "text",
                                       "her_words": words}))
        return {"ok": True}
    elif kind == "wake":
        await k.process(Event(type="device.wake", source="operator", payload={"trigger": "touch", "by": "operator"}))
    elif kind == "utterance":
        text = str(body.get("text") or "").strip()
        if not text or svc.text_loop is None:
            return {"ok": False, "reason": "bad_args"}
        r = await svc.text_loop.utterance(text)
        return {"ok": bool(r.get("ok", True)), **{x: r[x] for x in ("turn_id", "reason") if x in r}}
    elif kind == "play_voice":
        links = _links()
        if links is not None:
            r = await links.inject_voice_note(svc)
            if not r.get("ok"):
                return r
            vid = str(r.get("voice_id"))
        else:
            vid = str(body.get("voice_id") or "vm_demo")
        await k.process(Event(type="voice.play_request", source="agent",
                              payload={"message_id": vid}))          # plays directly: no announce, no offer
        return {"ok": True, "voice_id": vid, "played": True}
    elif kind == "birth":
        await k.process(op_event("operator.birth"))
        await k.emit(ui({"type": "birth"}, P1))
    elif kind == "warmup":
        await k.process(op_event("operator.warmup"))
        from .services import _import_optional
        aw = _import_optional("ahbo.agent.wiring")
        if aw is not None and hasattr(aw, "warmup"):
            r = aw.warmup(svc)
            if asyncio.iscoroutine(r):
                svc.spawn(r, name="warmup")
    else:
        return {"ok": False, "reason": "unknown_kind"}
    return {"ok": True}


# ---------------------------------------------------------------- overrides (§12.1 panel 4)
async def override(svc: Services, body: dict[str, Any]) -> dict[str, Any]:
    k = svc.kernel
    kind = body.get("kind")
    rest = {x: y for x, y in body.items() if x != "kind"}
    simple = {"incident_ok": "operator.ok", "incident_escalate": "operator.escalate",
              "incident_resolve": "operator.resolve", "force_tool_result": "operator.force_tool_result",
              "mute": "operator.mute", "unmute": "operator.unmute", "provider": "operator.provider_switch"}
    if kind in simple:
        await k.process(op_event(simple[kind], **rest))
        return {"ok": True}
    if kind == "play_fixed":
        clip = str(body.get("clip_id") or "")
        if clip not in svc.config.fixed_lines and not clip.startswith("R_"):
            return {"ok": False, "reason": "unknown_clip"}
        out = await k.process(op_event("operator.play_fixed", clip_id=clip))
        if not any(c.type == "device.play_fixed" for c in out):
            await k.emit(cmd("device.play_fixed", P1, clip_id=clip, say_id=f"say_op_{new_id('x')[2:]}"))
        return {"ok": True}
    if kind == "say":
        text = str(body.get("text") or "").strip()
        if not text:
            return {"ok": False, "reason": "bad_args"}
        out = await k.process(op_event("operator.say", text=text))
        if not any(c.type == "device.ui" and (c.payload.get("ui") or {}).get("type") == "say_start" for c in out):
            say_id = f"say_op_{new_id('x')[2:]}"
            dur = round(max(1.0, len(text) * 0.19), 2)
            await k.emit(ui({"type": "say_start", "id": say_id, "text": text, "from": "ahbo", "durationSec": dur}, P1))

            async def end() -> None:
                await asyncio.sleep(dur)
                await k.emit(ui({"type": "say_end", "id": say_id}, P1))
            svc.spawn(end(), name="operator_say")
        return {"ok": True}
    if kind == "panic":
        return await panic(svc)
    if kind in ("knobs", "interruption"):
        knobs = dict(svc.db.kv_get("knobs", {}) or {})
        allowed = ("flash_thinking", "sonnet_thinking", "interruption_enabled")
        for key in allowed:
            if key in body:
                knobs[key] = body[key]
        if kind == "interruption" and "on" in body:
            knobs["interruption_enabled"] = bool(body["on"])
        svc.db.kv_set("knobs", knobs)
        await k.process(op_event("operator.model_config", **{x: knobs.get(x) for x in allowed}))
        if "interruption_enabled" in knobs:
            await k.emit(cmd("device.config_update", P1, interruption_enabled=bool(knobs["interruption_enabled"])))
        return {"ok": True, "knobs": knobs}
    return {"ok": False, "reason": "unknown_kind"}
