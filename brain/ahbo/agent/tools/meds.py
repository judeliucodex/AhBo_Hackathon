"""Medicine tools (§4.3.2, INV-4). Facts come only from the Kernel's dose state (`med_schedule`,
`dose_events`), never from the model. get_med_status is read-only. confirm_taken / snooze_dose only *ask*: they go
through Kernel.request (`dose.confirm_request` / `dose.snooze_request`) and the dose reducer decides (INV-1)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ahbo.core import numwords as nw
from ahbo.core.jsonutil import loads

from . import UNAVAILABLE, ToolCtx, kernel_request, tool

TAKEN = "taken_self_reported"


def med_catalogue(ctx: ToolCtx) -> dict[str, dict[str, Any]]:
    return {r["id"]: {**r, "pill": loads(r["pill"], [])} for r in ctx.db.query("SELECT * FROM med_catalogue")}


def today_slots(ctx: ToolCtx) -> list[dict[str, Any]]:
    day = ctx.clock.today().isoformat()
    cat = med_catalogue(ctx)
    rows = ctx.db.query("SELECT s.slot_id, s.time, s.med_names, d.state, d.resolved_at FROM med_schedule s "
                        "LEFT JOIN dose_events d ON d.slot_id=s.slot_id AND d.date=? WHERE s.active=1 "
                        "ORDER BY s.time", (day,))
    out = []
    for r in rows:
        meds = [cat[m] for m in loads(r["med_names"], []) if m in cat]
        out.append({"slot_id": r["slot_id"], "time": r["time"], "state": r["state"] or "scheduled",
                    "resolved_at": r["resolved_at"], "meds": meds})
    return out


def med_card(m: dict[str, Any]) -> dict[str, Any]:
    return {"id": f"med_{m['id']}", "kind": "med", "label": m["yue"], "small": f"{m['en']} {m['dose']}".strip(),
            "pill": m.get("pill") or ["--pill-a", "--pill-a"], "when_candidates": [m["yue"]]}


class MedStatusArgs(BaseModel):
    model_config = {"extra": "ignore"}
    scope: Literal["next", "today"] = "next"


@tool("get_med_status", "查藥物時間表同今日食咗邊啲（只可以用呢個答藥嘅問題）。", MedStatusArgs)
async def get_med_status(ctx: ToolCtx, args: MedStatusArgs) -> dict[str, Any]:
    now = ctx.clock.now()
    hm = now.strftime("%H:%M")
    slots = today_slots(ctx)
    today = [{"slot_id": s["slot_id"], "time": s["time"], "time_words": nw.hhmm_words(s["time"], period=True),
              "state": s["state"], "meds": [m["yue"] for m in s["meds"]]} for s in slots]
    pending = [s for s in slots if s["state"] != TAKEN and (s["time"] >= hm or s["state"] in ("reminding", "snoozed"))]
    taken = [s["slot_id"] for s in slots if s["state"] == TAKEN]
    if not pending:
        return {"ok": True, "next": None, "today": today, "taken_today": taken,
                "_step": {"outcome": "none", "vars": {}}}
    nxt = pending[0]
    h, m = (int(x) for x in nxt["time"].split(":"))
    due_now = nxt["state"] in ("reminding", "snoozed") or nxt["time"] <= hm
    minutes = max(0.0, (now.replace(hour=h, minute=m, second=0, microsecond=0) - now).total_seconds() / 60)
    rel = "而家" if due_now else nw.rel_words(minutes)
    tw = nw.time_words(h, m, zheng=True)
    count = len(nxt["meds"])
    nxt_out = {"slot_id": nxt["slot_id"], "time": nxt["time"], "time_words": tw,
               "time_words_period": nw.time_words(h, m, period=True), "rel_words": rel, "due_now": due_now,
               "meds": [{"id": x["id"], "yue": x["yue"], "en": x["en"], "dose": x["dose"]} for x in nxt["meds"]],
               "count": count, "count_words": nw.int_words(count, counting=True)}
    cards = [{"id": "next_dose", "kind": "event", "icon": "⏰", "label": f"{nw.card_time(nxt['time'])} 食藥",
              "small": rel, "when_candidates": [tw, nw.time_words(h, m)]}]
    cards += [med_card(x) for x in nxt["meds"]]
    return {"ok": True, "next": nxt_out, "today": today, "taken_today": taken, "_cards": cards,
            "_step": {"outcome": "next", "vars": {"next_time_words": tw,
                                                  "count_words": nw.int_words(count, counting=True)}}}


# ---------------------------------------------------------------- Kernel-validated writes (dose flow, §4.3.2)
class ConfirmArgs(BaseModel):
    model_config = {"extra": "ignore"}
    slot_id: str | None = None
    utterance: str = ""


@tool("confirm_taken", "佢話已經食咗藥嗰陣用（由系統核實；冇到期嘅藥會答 no_dose_pending，唔好講已經記低）。",
      ConfirmArgs)
async def confirm_taken(ctx: ToolCtx, args: ConfirmArgs) -> dict[str, Any]:
    src = ctx.extra.get("source", "voice")
    res = await kernel_request(ctx, "dose.confirm_request", slot_id=args.slot_id, utterance=args.utterance[:200],
                               source=src if src in ("voice", "touch", "operator") else "voice")
    return res or dict(UNAVAILABLE)


class SnoozeArgs(BaseModel):
    model_config = {"extra": "ignore"}
    minutes: int = Field(10, ge=1, le=15)


@tool("snooze_dose", "佢想遲啲先食藥：延遲提醒（最多十五分鐘，每次藥最多兩次）。", SnoozeArgs)
async def snooze_dose(ctx: ToolCtx, args: SnoozeArgs) -> dict[str, Any]:
    res = await kernel_request(ctx, "dose.snooze_request", minutes=args.minutes)
    return res or dict(UNAVAILABLE)
