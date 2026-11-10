"""Concern (§4.3.4): non-fall health or safety worries. The red-flag matcher may only RAISE the level (INV-2).

Inputs: `agent.concern` (request from the `raise_concern` tool; reply `{ok, level}` with the effective level) and
the deterministic red-flag matcher on every final `user.utterance`.
- info      → today's digest list (kv `concern_digest`)
- urgent    → family.send INFO now + a P1 inject offering to call Ah Chun (once per category per 10 min)
- emergency → the fall-incident machine at AWAITING_OK (no photo)
- scam markers count as urgent (category `scam`).
During an open incident every concern is at least `emergency` (it can never lower it) and changes nothing.

Slice `state.concern`: {"recent": {category: iso}, "digest": [..]}.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ahbo.core.events import P1, Command, Event, cmd

from .. import arbiter
from ..common import family_contact_ids
from ..matchers import match_redflag, raise_level
from ..state import KernelState
from . import fall

URGENT_REPEAT_S = 600.0


def _slice(state: KernelState) -> dict[str, Any]:
    state.concern.setdefault("recent", {})
    state.concern.setdefault("digest", [])
    return state.concern


def handle(state: KernelState, ctx: Any, ev: Event, level: str, category: str, reason: str,
           origin: str) -> tuple[str, list[Command]]:
    """Apply a concern; returns (effective level, commands)."""
    c = _slice(state)
    flag = match_redflag(reason, ctx.config)
    eff = raise_level(level, flag[0] if flag else None)
    if flag and flag[1] == "scam":
        category = "scam"
    if state.incident_active:
        inc = fall.inc_of(state)
        if inc is not None:
            fall._note(ctx, inc, "concern", level=level, category=category, origin=origin)
        return "emergency", []
    entry = {"t": ctx.now.isoformat(), "level": eff, "category": category, "reason": reason[:200], "origin": origin}
    if eff == "info":
        c["digest"].append(entry)
        ctx.db.kv_set("concern_digest", {"date": ctx.now.date().isoformat(),
                                         "items": [d for d in c["digest"] if d["t"][:10] == ctx.now.date().isoformat()]})
        return eff, []
    if eff == "emergency":
        return eff, fall.start(state, ctx, ev, "concern", at="AWAITING_OK",
                               detail={"category": category, "reason": reason[:120], "origin": origin})
    # urgent
    last = c["recent"].get(category)
    if last and (ctx.now - datetime.fromisoformat(last)).total_seconds() < URGENT_REPEAT_S:
        return eff, []
    c["recent"][category] = ctx.now.isoformat()
    c["digest"].append(entry)
    what = "可能遇到電話騙案" if category == "scam" else "話身體唔舒服"
    out = [cmd("family.send", P1, ev.correlation_id, kind="INFO", contact_ids=family_contact_ids(ctx),
               text=f"王婆婆{what}：「{reason[:60]}」。請盡快打電話關心下佢。", category=category)]
    content = "我已經話咗俾阿珍知，佢會打俾你。" if category != "scam" else "呢啲電話要小心，我已經話咗俾阿珍知。"
    item = arbiter.make_item("家人", P1, content, "安慰佢，問佢使唔使叫阿珍即刻打電話俾佢。唔好診斷。",
                             corr=ev.correlation_id, after_turn=origin == "redflag")
    _, cmds = arbiter.request(state, ctx, item)
    return eff, out + cmds


def reduce(state: KernelState, ev: Event, ctx: Any) -> list[Command] | None:
    t, p = ev.type, ev.payload
    if t == "agent.concern":
        level = p.get("level") if p.get("level") in ("info", "urgent", "emergency") else "info"
        eff, out = handle(state, ctx, ev, level, str(p.get("category") or "other"), str(p.get("reason") or ""),
                          "agent")
        ctx.reply(ev, {"ok": True, "level": eff})
        return out
    if t == "user.utterance" and p.get("final", True):
        flag = match_redflag(str(p.get("text") or ""), ctx.config)
        if flag is None:
            return None
        _, out = handle(state, ctx, ev, flag[0], flag[1], str(p.get("text") or ""), "redflag")
        return out
    if t == "operator.reset":
        state.concern = {}
    return None
