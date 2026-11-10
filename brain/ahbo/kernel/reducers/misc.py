"""Console and bookkeeping events (§12, INV-8): every override is an `operator.*` event and its effects are commands
linked to it. Also device/beacon presence, `event.leave_soon`, and the session/face restart hook.

operator.reset          clear the flow slices, close the session, face reset (the platform reloads the seed)
operator.play_fixed     device.play_fixed {clip_id}
operator.say            a caption from Ah Bo, marked as the operator's (`say_start` + `say_end` timer)
operator.plan_now       → plan.due (the deep/plan component handles it)
operator.force_tool_result / provider_switch / model_config → kv `forced_tool_results` / `providers` / `knobs`
device.online/offline, sensor.heartbeat/lost → `state.extra["devices"|"beacons"]` (console only)
event.leave_soon        → arbiter P1 inject 提醒
(operator.ok/escalate/resolve/fall → fall; dose_now → dose; checkin_now → checkin; mute/unmute → session.)
"""
from __future__ import annotations

from typing import Any

from ahbo.core import numwords as nw
from ahbo.core.events import P0, P1, Command, Event, cmd, ui

from .. import arbiter
from ..common import later
from ..state import KernelState
from .session import close_session

OP_SAY_SECONDS_PER_CHAR = 0.19


def _kv_merge(ctx: Any, key: str, patch: dict[str, Any]) -> dict[str, Any]:
    cur = dict(ctx.db.kv_get(key, {}) or {})
    cur.update({k: v for k, v in patch.items() if v is not None})
    ctx.db.kv_set(key, cur)
    return cur


def _reset(state: KernelState, ctx: Any) -> list[Command]:
    out: list[Command] = []
    if state.session is not None:
        out += close_session(state, ctx, why="reset")
    state.incident = None
    state.turn = None
    state.family_active = False
    arbiter.clear(state)
    state.extra.pop("_arb_last_activity", None)
    out += [ui({"type": "reset"}, P0), ui({"type": "mood", "mood": "idle"}, P0), cmd("device.wake_enable", P1, on=True)]
    return out


def reduce(state: KernelState, ev: Event, ctx: Any) -> list[Command] | None:
    t, p, src = ev.type, ev.payload, ev.source
    ex = state.extra
    if t.startswith("operator.") and src != "operator":
        return None                                   # INV-2/INV-8: only the console speaks as the operator
    if t == "operator.reset":
        return _reset(state, ctx)
    if t == "operator.play_fixed":
        clip = str(p.get("clip_id") or "")
        if not clip or (clip not in ctx.config.fixed_lines and not clip.startswith("R_")):
            return None
        return [cmd("device.play_fixed", P1, clip_id=clip, say_id=f"say_op_{ev.id[4:]}", by="operator")]
    if t == "operator.say":
        text = str(p.get("text") or "").strip()
        if not text:
            return None
        say_id = f"say_op_{ev.id[4:]}"
        dur = round(max(1.0, len(text) * OP_SAY_SECONDS_PER_CHAR), 2)
        return [ui({"type": "say_start", "id": say_id, "text": text, "from": "ahbo", "durationSec": dur}, P1),
                cmd("timer.set", key=f"say_end:{say_id}", delay_s=dur + 0.15, data={"say_id": say_id})]
    if t == "operator.plan_now":
        later(ctx, "plan.due", {"date": ctx.now.date().isoformat(), "by": "operator"}, ev.correlation_id or ev.id,
              source="scheduler")
        return None
    if t == "operator.force_tool_result":
        name = str(p.get("name") or p.get("tool") or "")
        if name:
            _kv_merge(ctx, "forced_tool_results", {name: p.get("result", p)})
        return None
    if t == "operator.provider_switch":
        _kv_merge(ctx, "providers", dict(p))
        return None
    if t == "operator.model_config":
        _kv_merge(ctx, "knobs", {k: p.get(k) for k in ("flash_thinking", "sonnet_thinking", "interruption_enabled")})
        return None
    if t in ("operator.mute", "operator.unmute"):
        _kv_merge(ctx, "knobs", {"muted": t == "operator.mute"})
        return None
    if t in ("device.online", "device.offline", "device.heartbeat"):
        dev = str(p.get("device_id") or "?")
        d = ex.setdefault("devices", {}).setdefault(dev, {})
        d.update({"online": t != "device.offline", "at": ctx.now.isoformat()})
        if t == "device.offline" and state.incident_active and isinstance(state.incident, dict):
            from .fall import _note
            _note(ctx, state.incident, "device_offline", device_id=dev)
        return None
    if t == "sensor.heartbeat":
        bid = str(p.get("beacon_id") or "beacon1")
        ex.setdefault("beacons", {})[bid] = {"seen_mono": ctx.kernel.monotonic(), "at": ctx.now.isoformat(),
                                            "battery": p.get("battery"), "lost": False}
        return None
    if t == "sensor.lost":
        b = ex.setdefault("beacons", {}).setdefault(str(p.get("beacon_id") or "beacon1"), {})
        b["lost"] = True
        return None
    if t == "event.leave_soon":
        row = ctx.db.one("SELECT * FROM calendar_events WHERE id=?", (p.get("event_id"),)) if p.get("event_id") else None
        when = nw.hhmm_words(row["datetime"][11:16], period=True) if row else ""
        item = arbiter.make_item("提醒", P1, "差唔多夠鐘出門喇，記得帶齊嘢呀。",
                                 f"{when}要出門：{(row or {}).get('title', '')}。" if row else "",
                                 corr=ev.correlation_id)
        _, out = arbiter.request(state, ctx, item)
        return out or None
    return None


# ---------------------------------------------------------------- restart: sessions and the face mirror
def _loaded(kernel: Any) -> None:
    st, face = kernel.state, kernel.ui
    if st.session is None:
        # the agent's connection died with the process: a session left open in the DB is over
        kernel.db.execute("UPDATE sessions SET closed_at=? WHERE closed_at IS NULL", (kernel.clock.iso(),))
        if face.mood in ("listening", "thinking", "curious"):
            face.mood = "idle"            # no stale `listening` with no session (§23.3)
        for gid in [g for g in face.groups if g != "fall"]:
            face.groups.pop(gid, None)    # a turn's step group cannot finish any more
    if not st.incident_active:
        if face.mood == "alarm":
            face.mood = "idle"
        face.ok_button = False


def setup(kernel: Any) -> None:
    if _loaded not in kernel.loaded_hooks:
        kernel.loaded_hooks.append(_loaded)

