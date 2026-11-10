"""Dose lifecycle (§4.3.2, §24.9, INV-4). Medicine facts live here and in `dose_events`; nothing else decides them.

    SCHEDULED ─ dose.due (T) | operator.dose_now ─► REMINDING  (cards, R_<slot> clip, reminder session, inject 提醒)
    REMINDING ─ re-prompt at T+15, T+30 ─ MISSED at T+60 (family INFO)
    REMINDING ─ dose.snooze_request (≤ 2 × ≤ 15 min) ─► SNOOZED ─ until ─► REMINDING (re-prompt)
    REMINDING | SNOOZED | MISSED (within 3 h of T) ─ dose.confirm_request | reminder_confirmed ─► taken_self_reported

Timing is checked on `schedule.tick` against the virtual clock (so it survives restarts and clock jumps).
Slice `state.doses`: {slot_id: record} for today; every change is written through to `dose_events`.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ahbo.core import numwords as nw
from ahbo.core.events import P1, Command, Event, cmd
from ahbo.core.ids import new_id
from ahbo.core.jsonutil import dumps, loads

from .. import arbiter
from ..common import family_contact_ids
from ..state import KernelState

TAKEN = "taken_self_reported"
OPEN = ("reminding", "snoozed", "missed")
CONFIRM_WINDOW_H = 3.0
REPROMPT_MIN = (15, 30)
MISSED_MIN = 60
MAX_SNOOZES = 2
MAX_SNOOZE_MIN = 15


# ---------------------------------------------------------------- data
def _slot(ctx: Any, slot_id: str) -> dict[str, Any] | None:
    return ctx.db.one("SELECT * FROM med_schedule WHERE slot_id=? AND active=1", (slot_id,))


def _meds(ctx: Any, slot: dict[str, Any]) -> list[dict[str, Any]]:
    cat = {r["id"]: r for r in ctx.db.query("SELECT * FROM med_catalogue")}
    return [cat[m] for m in loads(slot["med_names"], []) if m in cat]


def _due_at(ctx: Any, slot: dict[str, Any], day: str) -> datetime:
    h, m = (int(x) for x in slot["time"].split(":"))
    return datetime.fromisoformat(day).replace(hour=h, minute=m, tzinfo=ctx.now.tzinfo)


def _save(ctx: Any, r: dict[str, Any]) -> None:
    r["updated_at"] = ctx.now.isoformat()
    ctx.db.execute(
        "INSERT INTO dose_events(id,slot_id,date,state,reminded_at,snoozes,snoozed_until,resolved_at,source,utterance,"
        "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(slot_id,date) DO UPDATE SET state=excluded.state, "
        "reminded_at=excluded.reminded_at, snoozes=excluded.snoozes, snoozed_until=excluded.snoozed_until, "
        "resolved_at=excluded.resolved_at, source=excluded.source, utterance=excluded.utterance, "
        "updated_at=excluded.updated_at",
        (r["id"], r["slot_id"], r["date"], r["state"], dumps(r["reminded_at"]), r["snoozes"], r.get("snoozed_until"),
         r.get("resolved_at"), r.get("source"), r.get("utterance"), r["updated_at"]))


def _from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": row["id"], "slot_id": row["slot_id"], "date": row["date"], "state": row["state"],
            "reminded_at": loads(row.get("reminded_at"), []) or [], "snoozes": int(row.get("snoozes") or 0),
            "snoozed_until": row.get("snoozed_until"), "resolved_at": row.get("resolved_at"),
            "source": row.get("source"), "utterance": row.get("utterance"), "due_at": None, "reprompts": None}


def reload(state: KernelState, db: Any, today: str) -> None:
    state.doses = {r["slot_id"]: _from_row(r) for r in db.query("SELECT * FROM dose_events WHERE date=?", (today,))}
    state.extra["dose_day"] = today


def _rec(state: KernelState, ctx: Any, slot_id: str) -> dict[str, Any]:
    day = ctx.now.date().isoformat()
    if state.extra.get("dose_day") != day:
        reload(state, ctx.db, day)
    r = state.doses.get(slot_id)
    if r is None:
        r = {"id": new_id("dose"), "slot_id": slot_id, "date": day, "state": "scheduled", "reminded_at": [],
             "snoozes": 0, "snoozed_until": None, "resolved_at": None, "source": None, "utterance": None,
             "due_at": None, "reprompts": None}
        state.doses[slot_id] = r
    return r


def _due(ctx: Any, r: dict[str, Any]) -> datetime | None:
    if r.get("due_at"):
        return datetime.fromisoformat(r["due_at"])
    slot = _slot(ctx, r["slot_id"])
    return _due_at(ctx, slot, r["date"]) if slot else None


def pending(state: KernelState, ctx: Any, slot_id: str | None = None,
            states: tuple[str, ...] = OPEN, at: datetime | None = None) -> dict[str, Any] | None:
    """The dose she may confirm now: open, due, and within 3 h of its due time (latest first)."""
    day = ctx.now.date().isoformat()
    if state.extra.get("dose_day") != day:
        reload(state, ctx.db, day)
    when = at or ctx.now
    best = None
    for r in state.doses.values():
        if slot_id and r["slot_id"] != slot_id:
            continue
        if r["state"] not in states:
            continue
        due = _due(ctx, r)
        if due is None or due - timedelta(minutes=30) > when or (when - due).total_seconds() > CONFIRM_WINDOW_H * 3600:
            continue
        if best is None or due > _due(ctx, best):
            best = r
    return best


# ---------------------------------------------------------------- reminders
def _cards(ctx: Any, slot: dict[str, Any]) -> list[dict[str, Any]]:
    cards = [{"id": "next_dose", "kind": "event", "icon": "⏰", "label": f"{nw.card_time(slot['time'])} 食藥",
              "small": "而家"}]
    for m in _meds(ctx, slot):
        cards.append({"id": f"med_{m['id']}", "kind": "med", "label": m["yue"],
                      "small": f"{m.get('en') or ''} {m.get('dose') or ''}".strip(),
                      "pill": loads(m.get("pill"), []) or ["--pill-a", "--pill-a"]})
    return cards


def _remind(state: KernelState, ctx: Any, r: dict[str, Any], *, first: bool, corr: str | None) -> list[Command]:
    slot = _slot(ctx, r["slot_id"])
    if slot is None:
        return []
    names = "、".join(m["yue"] for m in _meds(ctx, slot))
    tw = nw.hhmm_words(slot["time"], zheng=True)
    r["reminded_at"].append(ctx.now.isoformat())
    content = "食咗之後記得話我知呀。" if first else "提提你，記得食藥呀，食咗話我知。"
    item = arbiter.make_item("提醒", P1, content, f"而家係{tw}，提佢食{names}，問佢食咗未。",
                             clip=f"R_{r['slot_id']}", cards=_cards(ctx, slot), dose=True, slot_id=r["slot_id"],
                             session={"reason": "dose", "mode": "reminder"}, corr=corr)
    _, out = arbiter.request(state, ctx, item)
    return out


def start_reminder(state: KernelState, ctx: Any, slot_id: str, corr: str | None, *, due_now: bool = False
                   ) -> list[Command]:
    if _slot(ctx, slot_id) is None:
        return []
    r = _rec(state, ctx, slot_id)
    if r["state"] == TAKEN or (r["state"] in OPEN and not due_now):
        return []
    r["state"] = "reminding"
    r["reprompts"] = 0
    if due_now:
        r["due_at"] = ctx.now.isoformat()
    out = _remind(state, ctx, r, first=True, corr=corr)
    _save(ctx, r)
    return out


def _tick(state: KernelState, ctx: Any) -> list[Command]:
    day = ctx.now.date().isoformat()
    if state.extra.get("dose_day") != day:
        reload(state, ctx.db, day)
    out: list[Command] = []
    for r in list(state.doses.values()):
        due = _due(ctx, r)
        if due is None:
            continue
        mins = (ctx.now - due).total_seconds() / 60
        if r["state"] == "snoozed" and r.get("snoozed_until") and ctx.now >= datetime.fromisoformat(r["snoozed_until"]):
            r["state"] = "reminding"
            out += _remind(state, ctx, r, first=False, corr=r["id"])
            _save(ctx, r)
            continue
        if r["state"] != "reminding":
            continue
        if r.get("reprompts") is None:          # rebuilt from the DB: count what was already said
            r["reprompts"] = max(0, len(r["reminded_at"]) - 1)
        if mins >= MISSED_MIN:
            r["state"] = "missed"
            _save(ctx, r)
            slot = _slot(ctx, r["slot_id"]) or {"time": "?"}
            names = "、".join(m["yue"] for m in _meds(ctx, slot)) if slot.get("med_names") else ""
            out.append(cmd("family.send", P1, r["id"], kind="INFO", contact_ids=family_contact_ids(ctx),
                           text=f"王婆婆仲未確認食咗 {slot['time']} 嗰次藥（{names}）。", slot_id=r["slot_id"]))
            out += _drop_queued(state, r["slot_id"])
        elif r["reprompts"] < len(REPROMPT_MIN) and mins >= REPROMPT_MIN[r["reprompts"]]:
            r["reprompts"] += 1
            out += _remind(state, ctx, r, first=False, corr=r["id"])
            _save(ctx, r)
    return out


def _drop_queued(state: KernelState, slot_id: str) -> list[Command]:
    q = arbiter.slice_(state)["queue"]
    item = q.get("1")
    if item and item.get("slot_id") == slot_id:
        q.pop("1", None)
        return [cmd("timer.cancel", key=f"arb:p1:{item['id']}")]
    return []


# ---------------------------------------------------------------- confirmations
def confirm(state: KernelState, ctx: Any, r: dict[str, Any], source: str, utterance: str | None,
            at: str | None = None) -> list[Command]:
    r["state"] = TAKEN
    r["resolved_at"] = at or ctx.now.isoformat()
    r["source"] = source
    r["utterance"] = (utterance or "")[:200] or None
    r["snoozed_until"] = None
    _save(ctx, r)
    return _drop_queued(state, r["slot_id"])


def _next_slot(ctx: Any) -> str | None:
    rows = ctx.db.query("SELECT slot_id, time FROM med_schedule WHERE active=1 ORDER BY time")
    if not rows:
        return None
    hm = ctx.now.strftime("%H:%M")
    later = [r for r in rows if r["time"] >= hm]
    return (later[0] if later else rows[-1])["slot_id"]


def reduce(state: KernelState, ev: Event, ctx: Any) -> list[Command] | None:
    t, p = ev.type, ev.payload
    if t == "dose.due":
        if p.get("date") and p["date"] != ctx.now.date().isoformat():
            return None
        return start_reminder(state, ctx, str(p.get("slot_id") or ""), ev.correlation_id)
    if t == "operator.dose_now" and ev.source == "operator":
        sid = p.get("slot_id") or _next_slot(ctx)
        return start_reminder(state, ctx, sid, ev.correlation_id, due_now=True) if sid else None
    if t == "schedule.tick":
        return _tick(state, ctx) or None
    if t == "dose.confirm_request":
        r = pending(state, ctx, p.get("slot_id") or None)
        if r is None and p.get("slot_id"):
            r = pending(state, ctx, None)          # the model guessed the wrong slot: the Kernel knows which is due
        if r is None:
            ctx.reply(ev, {"ok": False, "reason": "no_dose_pending"})
            return None
        src = p.get("source") if p.get("source") in ("voice", "touch", "operator") else "voice"
        out = confirm(state, ctx, r, src, p.get("utterance"))
        ctx.reply(ev, {"ok": True, "slot_id": r["slot_id"], "status": TAKEN})
        return out
    if t == "dose.snooze_request":
        r = pending(state, ctx, p.get("slot_id") or None, states=("reminding", "snoozed"))
        if r is None:
            ctx.reply(ev, {"ok": False, "reason": "no_dose_pending"})
            return None
        if r["snoozes"] >= MAX_SNOOZES:
            ctx.reply(ev, {"ok": False, "reason": "max_snoozes"})
            return None
        try:
            minutes = max(1, min(MAX_SNOOZE_MIN, int(p.get("minutes") or MAX_SNOOZE_MIN)))
        except (TypeError, ValueError):
            minutes = MAX_SNOOZE_MIN
        until = ctx.now + timedelta(minutes=minutes)
        r["snoozes"] += 1
        r["state"] = "snoozed"
        r["snoozed_until"] = until.isoformat()
        _save(ctx, r)
        ctx.reply(ev, {"ok": True, "slot_id": r["slot_id"], "until": until.isoformat(timespec="seconds"),
                       "minutes": minutes})
        return _drop_queued(state, r["slot_id"]) or None
    if t == "device.face_event" and ev.source == "device":
        e = p.get("event") or {}
        if e.get("type") == "reminder_confirmed":      # touched on the iPad while offline (§10.4), synced later
            at = None
            try:
                at = datetime.fromisoformat(str(e.get("at"))) if e.get("at") else None
            except ValueError:
                at = None
            r = pending(state, ctx, e.get("slot_id") or None, states=("scheduled",) + OPEN, at=at)
            if r is None and e.get("slot_id") and _slot(ctx, e["slot_id"]):
                r = _rec(state, ctx, e["slot_id"])          # never reminded online: the mirror played it
                due = _due(ctx, r)
                if due is None or abs(((at or ctx.now) - due).total_seconds()) > CONFIRM_WINDOW_H * 3600:
                    return None
            if r is None or r["state"] == TAKEN:
                return None
            return confirm(state, ctx, r, e.get("source") or "touch_offline", None,
                           at.isoformat() if at else None) or None
    if t == "operator.reset":
        state.doses = {}
        state.extra.pop("dose_day", None)
    return None


def _loaded(kernel: Any) -> None:
    reload(kernel.state, kernel.db, kernel.clock.today().isoformat())


def setup(kernel: Any) -> None:
    if _loaded not in kernel.loaded_hooks:
        kernel.loaded_hooks.append(_loaded)
