"""Quick read-only tools (§5.5, §26.6): time, weather, calendar, day plan, route, activities, voice notes.
Each ≤ 300 ms, read-only (except play_voice_message, which asks the Kernel to play)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel

from ahbo.core import numwords as nw
from ahbo.core.clock import HKT
from ahbo.core.config import load_seed
from ahbo.core.events import Event
from ahbo.core.jsonutil import loads

from . import ToolCtx, tool

WEEKDAYS = "一二三四五六日"
DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
KIND_ICON = {"medical": "🏥", "meal": "🍵"}


def _hm(dt_iso: str) -> tuple[int, int]:
    dt = datetime.fromisoformat(dt_iso)
    return dt.hour, dt.minute


@tool("get_time", "現在的日期、星期同時間（香港時間）。")
async def get_time(ctx: ToolCtx, _args) -> dict[str, Any]:
    now = ctx.clock.now()
    return {"ok": True, "date": now.date().isoformat(), "weekday_words": "星期" + WEEKDAYS[now.weekday()],
            "time": now.strftime("%H:%M"), "time_words": nw.time_words(now.hour, now.minute, period=True),
            "period": nw.period_words(now.hour)}


# ---------------------------------------------------------------- calendar
class ListEventsArgs(BaseModel):
    model_config = {"extra": "ignore"}
    date: str | None = None


def _events_for(ctx: ToolCtx, day: str) -> list[dict[str, Any]]:
    return ctx.db.query("SELECT * FROM calendar_events WHERE substr(datetime,1,10)=? AND status!='cancelled' "
                        "ORDER BY datetime", (day,))


def event_card(e: dict[str, Any]) -> dict[str, Any]:
    h, m = _hm(e["datetime"])
    kind = "plan" if e["kind"] == "plan" else "event"
    card: dict[str, Any] = {"id": f"ev_{e['id']}", "kind": kind, "label": f"{nw.card_time(f'{h}:{m:02d}')} {e['title']}",
                            "small": e.get("place") or None,
                            "when_candidates": [e["title"], e.get("place"), nw.time_words(h, m)]}
    if kind == "event":
        card["icon"] = KIND_ICON.get(e["kind"], "📅")
    return card


@tool("list_events", "睇今日（或指定日子）嘅日程同約會。", ListEventsArgs)
async def list_events(ctx: ToolCtx, args: ListEventsArgs) -> dict[str, Any]:
    day = args.date if args.date and args.date != "today" else ctx.clock.today().isoformat()
    evs = _events_for(ctx, day)
    out = []
    for e in evs:
        h, m = _hm(e["datetime"])
        out.append({"id": e["id"], "time": f"{h:02d}:{m:02d}", "time_words": nw.time_words(h, m, period=True),
                    "title": e["title"], "place": e.get("place"), "kind": e["kind"], "status": e["status"],
                    "route": e.get("route")})
    appts = [e for e in out if e["kind"] != "plan"]
    n = len(appts)
    return {"ok": True, "date": day, "events": out, "count": n, "count_words": nw.int_words(n, counting=True),
            "_cards": [event_card(e) for e in evs],
            "_step": {"outcome": "some" if n else "none", "vars": {"count_words": nw.int_words(n, counting=True)}}}


@tool("get_day_plan", "攞今日朝早七點準備好嘅計劃。")
async def get_day_plan(ctx: ToolCtx, _args) -> dict[str, Any]:
    day = ctx.clock.today().isoformat()
    row = ctx.db.one("SELECT * FROM day_plans WHERE date=?", (day,))
    if not row:
        return {"ok": True, "date": day, "summary": None, "plan": None, "_step": {"outcome": "none", "vars": {}}}
    return {"ok": True, "date": day, "summary": row["summary"], "plan": loads(row["json"], {}),
            "_step": {"outcome": "ok", "vars": {}}}


class RouteArgs(BaseModel):
    model_config = {"extra": "ignore"}
    destination: str


def place_short(place: str) -> str:
    for prefix in ("基督教", "香港"):
        if place.startswith(prefix) and len(place) > len(prefix) + 1:
            place = place[len(prefix):]
    return place[:6]


@tool("get_route", "查去某個地方嘅巴士路線（由日程或者今日計劃）。", RouteArgs)
async def get_route(ctx: ToolCtx, args: RouteArgs) -> dict[str, Any]:
    dest = args.destination.strip()
    evs = _events_for(ctx, ctx.clock.today().isoformat())
    target = next((e for e in evs if e.get("place") and (e["place"] in dest or dest in (e.get("place_full") or "")
                                                         or dest in e["place"])), None)
    route = next((e.get("route") for e in evs if e.get("route") and (target is None or (target.get("place") or "")
                                                                    in (e.get("route") or ""))), None)
    short = place_short(target["place"] if target else dest)
    if not route:
        return {"ok": False, "reason": "no_route", "place_short": short,
                "_step": {"outcome": "failed", "vars": {"place_short": short}}}
    return {"ok": True, "destination": dest, "place_short": short, "route_text": route,
            "_step": {"outcome": "ok", "vars": {"place_short": short}}}


# ---------------------------------------------------------------- activities
def activities() -> list[dict[str, Any]]:
    return load_seed("activities.yaml") or []


def _act_icon(a: dict[str, Any]) -> str:
    title = a.get("title", "")
    if "粵曲" in title or "音樂" in a.get("interests", []):
        return "🎤"
    if "運動" in a.get("interests", []):
        return "🌳"
    return "🎨"


def activity_card(a: dict[str, Any]) -> dict[str, Any]:
    can_book = (a.get("signup") or {}).get("method") == "web_form"
    venue = a.get("venue_short") or a.get("venue")
    return {"id": f"act_{a['id']}", "kind": "activity", "icon": _act_icon(a),
            "label": f"{nw.card_time(a['time'])} {a['title']}",
            "small": f"{venue} · 可代報名" if can_book else venue, "when_candidates": [a["title"]]}


class FindArgs(BaseModel):
    model_config = {"extra": "ignore"}
    interest: str | None = None
    when: Literal["morning", "afternoon", "evening", "today"] | None = None


def _in_window(hhmm: str, when: str | None) -> bool:
    h = int(hhmm.split(":")[0])
    return {"morning": h < 12, "afternoon": 12 <= h < 18, "evening": h >= 18}.get(when or "today", True)


@tool("find_local_activities", "搵附近適合長者嘅活動（例如粵曲班），可以按興趣同時段揀。", FindArgs)
async def find_local_activities(ctx: ToolCtx, args: FindArgs) -> dict[str, Any]:
    now = ctx.clock.now()
    day = DAY_KEYS[now.weekday()]
    found = []
    for a in activities():
        if day not in a.get("days", DAY_KEYS) or not _in_window(a["time"], args.when):
            continue
        if a["time"] <= now.strftime("%H:%M"):
            continue
        if args.interest and not any(args.interest in i or i in args.interest
                                     for i in a.get("interests", []) + [a["title"]]):
            continue
        found.append(a)
    # Seeded preference (粵曲) first, then by time
    found.sort(key=lambda a: (0 if "粵曲" in a.get("interests", []) else 1, a["time"]))
    out = [{"id": a["id"], "title": a["title"], "venue": a["venue"], "venue_short": a.get("venue_short"),
            "time": a["time"], "time_words": nw.hhmm_words(a["time"], period=True),
            "walk_minutes": a.get("walk_minutes"), "interests": a.get("interests", []),
            "signup": {k: v for k, v in (a.get("signup") or {}).items()}} for a in found]
    if not out:
        return {"ok": True, "activities": [], "top_title": None, "_step": {"outcome": "none", "vars": {}}}
    top = found[0]
    top_title = nw.hhmm_words(top["time"]) + top["title"]
    return {"ok": True, "activities": out, "top_title": top_title, "_cards": [activity_card(top)], "_tone": "propose",
            "_step": {"outcome": "some", "vars": {"top_title": top_title}}}


class LogisticsArgs(BaseModel):
    model_config = {"extra": "ignore"}
    activity_id: str


@tool("check_activity_logistics", "檢查某個活動嘅時間有冇撞、行路要幾耐。", LogisticsArgs)
async def check_activity_logistics(ctx: ToolCtx, args: LogisticsArgs) -> dict[str, Any]:
    a = next((x for x in activities() if x["id"] == args.activity_id), None)
    if a is None:
        return {"ok": False, "reason": "unknown_activity", "_step": {"outcome": "failed", "vars": {}}}
    today = ctx.clock.today()
    h, m = (int(x) for x in a["time"].split(":"))
    start = datetime.combine(today, datetime.min.time(), HKT).replace(hour=h, minute=m)
    conflicts = []
    for e in _events_for(ctx, today.isoformat()):
        et = datetime.fromisoformat(e["datetime"])
        if abs((et - start).total_seconds()) < 90 * 60:
            conflicts.append({"id": e["id"], "title": e["title"], "time": et.strftime("%H:%M")})
    free = not conflicts
    period = nw.period_words(h)
    free_words = f"{period}得閒" if free else f"{period}有約"
    walk = int(a.get("walk_minutes") or 0)
    travel_words = f"行路{nw.int_words(walk)}分鐘" if walk else "要搭車"
    return {"ok": True, "activity_id": a["id"], "free": free, "conflicts": conflicts, "walk_minutes": walk,
            "free_words": free_words, "travel_words": travel_words,
            "_step": {"outcome": "ok", "vars": {"free_words": free_words, "travel_words": travel_words}}}


# ---------------------------------------------------------------- voice notes (S1)
def voice_card(vm: dict[str, Any], now: datetime) -> dict[str, Any]:
    rec = datetime.fromisoformat(vm["received_at"]) if vm.get("received_at") else now
    return {"id": f"vn_{vm['id']}", "kind": "voice_note", "label": f"{vm.get('from_name') or '家人'}嘅錄音",
            "small": nw.day_part_short(rec, now), "when_candidates": ["錄音"]}


@tool("list_voice_messages", "睇吓有冇家人留低嘅語音訊息。")
async def list_voice_messages(ctx: ToolCtx, _args) -> dict[str, Any]:
    now = ctx.clock.now()
    since = (now - timedelta(days=2)).isoformat()
    rows = ctx.db.query("SELECT * FROM voice_messages WHERE direction='in' AND received_at>=? "
                        "ORDER BY (played_at IS NOT NULL), received_at DESC", (since,))
    msgs = [{"id": r["id"], "from_name": r["from_name"], "received_words": nw.day_part_short(
        datetime.fromisoformat(r["received_at"]), now), "duration_s": r.get("duration_s"),
        "played": r.get("played_at") is not None} for r in rows]
    new = [r for r in rows if r.get("played_at") is None]
    if not new:
        return {"ok": True, "messages": msgs, "has_new": False, "from_name": None,
                "_step": {"outcome": "none", "vars": {}}}
    first = new[0]
    return {"ok": True, "messages": msgs, "has_new": True, "from_name": first["from_name"],
            "_cards": [voice_card(first, now)], "_tone": "family",
            "_step": {"outcome": "has_new", "vars": {"from_name": first["from_name"]}}}


class PlayArgs(BaseModel):
    model_config = {"extra": "ignore"}
    id: str


@tool("play_voice_message", "喺部機播一段家人嘅語音訊息。播完之前唔好講嘢。", PlayArgs)
async def play_voice_message(ctx: ToolCtx, args: PlayArgs) -> dict[str, Any]:
    vm = ctx.db.one("SELECT id FROM voice_messages WHERE id=?", (args.id,))
    if not vm:
        return {"ok": False, "reason": "not_found"}
    await ctx.kernel.process(Event(type="voice.play_request", source="agent", correlation_id=ctx.session_id,
                                   payload={"message_id": args.id, "card_id": f"vn_{args.id}"}))
    return {"ok": True, "id": args.id, "started": True, "_tone": "family"}
