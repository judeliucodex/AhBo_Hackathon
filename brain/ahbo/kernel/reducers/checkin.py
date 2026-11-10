"""Check-ins (§4.3.3, D11): morning / afternoon / evening, through the arbiter at P3.

Policy: never in quiet hours or during an incident; skip if a session ended < 20 min ago; if a session is open,
defer (re-tried on every `schedule.tick`) for up to 30 min, then drop. `operator.checkin_now` skips the
20-minute and open-session rules (still not during an incident).
Delivery: mood curious → session (reason `checkin_<kind>`) → inject 問候; no reply within 20 s → the session closes
(the card stays; no nagging repeat).

Slice `state.checkins`: {"deferred": {kind: first_due_iso}, "active": {session_id, kind} | None}.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ahbo.core.events import P3, Command, Event, cmd

from .. import arbiter
from ..common import note, quiet_hours
from ..state import KernelState
from .session import close_session

NO_REPLY_S = 20.0
KINDS = ("morning", "afternoon", "evening")
LINES = {
    "morning": ("早晨呀王婆婆，今日覺得點呀？",
                "朝早問候：可以講今日天氣（get_weather）、今日計劃（get_day_plan）同第一個約會；有未聽嘅錄音就提佢。"),
    "afternoon": ("王婆婆，下晝好呀，今日過成點呀？",
                  "下晝問候：可以傾吓舊事（recall_life_story）或者今日計劃入面嘅活動；問佢使唔使同阿珍講聲好。"),
    "evening": ("王婆婆，夜晚好呀，今晚食咗飯未呀？",
                "夜晚問候：講晚安，用 get_med_status 睇吓夜晚啲藥，講吓聽日第一件事。"),
}


def _slice(state: KernelState) -> dict[str, Any]:
    state.checkins.setdefault("deferred", {})
    state.checkins.setdefault("active", None)
    return state.checkins


def _skip(ctx: Any, kind: str, reason: str) -> None:
    note(ctx, "checkin.skipped", {"kind": kind, "reason": reason})


def _try(state: KernelState, ctx: Any, ev: Event, kind: str, force: bool) -> list[Command]:
    c = _slice(state)
    if quiet_hours(ctx):
        c["deferred"].pop(kind, None)
        _skip(ctx, kind, "quiet_hours")
        return []
    if state.incident_active:
        c["deferred"].pop(kind, None)
        _skip(ctx, kind, "incident")
        return []
    if not force:
        if state.session is not None:
            first = c["deferred"].setdefault(kind, ctx.now.isoformat())
            if (ctx.now - datetime.fromisoformat(first)).total_seconds() > 30 * 60:
                c["deferred"].pop(kind, None)
                _skip(ctx, kind, "session_open_30min")
            return []
        last = state.last_session_closed_at
        if last is not None and (ctx.now - last).total_seconds() < 20 * 60:
            c["deferred"].pop(kind, None)
            _skip(ctx, kind, "session_recent")
            return []
    c["deferred"].pop(kind, None)
    content, instruction = LINES[kind]
    item = arbiter.make_item("問候", P3, content, instruction, mood="curious", force=force,
                             session={"reason": f"checkin_{kind}", "mode": "companion"}, corr=ev.correlation_id)
    outcome, out = arbiter.request(state, ctx, item)
    if outcome == "now" and state.session is not None:
        c["active"] = {"session_id": state.session.id, "kind": kind}
        out.append(cmd("timer.set", key=f"checkin:noreply:{state.session.id}", delay_s=NO_REPLY_S,
                       data={"session_id": state.session.id}))
    return out


def reduce(state: KernelState, ev: Event, ctx: Any) -> list[Command] | None:
    t, p = ev.type, ev.payload
    c = _slice(state)
    if t == "checkin.due":
        kind = p.get("kind")
        if kind in KINDS:
            return _try(state, ctx, ev, kind, force=False) or None
        return None
    if t == "operator.checkin_now" and ev.source == "operator":
        kind = p.get("checkin") or p.get("kind") or "morning"
        return _try(state, ctx, ev, kind if kind in KINDS else "morning", force=True) or None
    if t == "schedule.tick" and c["deferred"] and state.session is None:
        out: list[Command] = []
        for kind in list(c["deferred"]):
            out += _try(state, ctx, ev, kind, force=False)
        return out or None
    act = c.get("active")
    if act and t == "user.utterance" and p.get("session_id") in (None, act["session_id"]):
        c["active"] = None
        return [cmd("timer.cancel", key=f"checkin:noreply:{act['session_id']}")]
    if t == "timer.fired" and p.get("key", "").startswith("checkin:noreply:"):
        sid = (p.get("data") or {}).get("session_id")
        if act and act["session_id"] == sid:
            c["active"] = None
            if state.turn is not None and state.turn.replying:
                c["active"] = act               # still speaking the greeting: check again shortly
                return [cmd("timer.set", key=f"checkin:noreply:{sid}", delay_s=5.0, data={"session_id": sid})]
            if state.session is not None and state.session.id == sid and not state.incident_active:
                return close_session(state, ctx, why="checkin_no_reply")
        return None
    if t == "operator.reset":
        state.checkins = {}
    return None

