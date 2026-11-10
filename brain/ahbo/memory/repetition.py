"""Repetition-aware replies (§7.4).

Each final `user.utterance` is tagged with one intent from a closed set (via `ahbo.agent.intent.tag` when that
module exists, otherwise a deterministic keyword fallback), and `repetition[date][intent] += 1`. From the third
time the same question is asked on one day:
  - `repetition_hint(db, clock)` gives the context builder 「佢今日第N次問呢個問題，…」;
  - a persistent `info` card (`rep_<intent>`) shows the answer so she can look instead of asking. Its text is
    computed from the Kernel's own data (med_schedule/dose_events, calendar, clock), never from a model (INV-4).
`trend_summary(db, date)` gives the digest its trend lines ("Asked about today's plan 6× (avg 2×)").
"""
from __future__ import annotations

import importlib
import inspect
import logging
from datetime import date, datetime, timedelta
from typing import Any

from ahbo.core import numwords as nw
from ahbo.core.clock import Clock
from ahbo.core.db import Database
from ahbo.core.jsonutil import loads

log = logging.getLogger("ahbo.memory.repetition")

INTENTS = ("ask_med_time", "ask_today", "ask_date", "ask_family_visit", "ask_where_item", "other")
THRESHOLD = 3
HINT = "佢今日第{n}次問呢個問題，請好似第一次咁耐心答，可以順便講多句安心嘅說話。"
LAST_KEY = "repetition_last"
TAKEN = "taken_self_reported"
WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")

LABEL_ZH = {"ask_med_time": "問食藥時間", "ask_today": "問今日安排", "ask_date": "問今日幾號",
            "ask_family_visit": "問家人幾時嚟", "ask_where_item": "問啲嘢放咗喺邊"}
LABEL_EN = {"ask_med_time": "Asked when to take medicine", "ask_today": "Asked about today's plan",
            "ask_date": "Asked the date", "ask_family_visit": "Asked when family will visit",
            "ask_where_item": "Asked where something is"}


# ---------------------------------------------------------------- tagging
def _has(t: str, *ws: str) -> bool:
    return any(w in t for w in ws)


def keyword_intent(text: str) -> str:
    """Deterministic fallback tagger (order matters: 今日幾號 is a date question, not a plan question)."""
    t = (text or "").replace(" ", "")
    if _has(t, "幾號", "星期幾", "禮拜幾", "幾月", "咩日子", "今日係咩日", "邊日"):
        return "ask_date"
    if "藥" in t:
        return "ask_med_time"
    if _has(t, "放咗喺邊", "放喺邊", "喺邊度", "搵唔到", "唔見咗", "唔記得放"):
        return "ask_where_item"
    if _has(t, "阿珍", "阿明", "女兒", "個女", "個仔", "孫") and _has(t, "幾時", "嚟", "探", "返", "見"):
        return "ask_family_visit"
    if _has(t, "有咩安排", "今日做咩", "今日有咩做", "今日要做咩", "有咩約", "幾點出門", "今日去邊", "要去邊",
            "今日有咩", "安排"):
        return "ask_today"
    return "other"


async def tag_intent(text: str) -> str:
    """`ahbo.agent.intent.tag(text)` if it exists (sync or async; str or {"intent": …}), else the keyword tagger."""
    try:
        mod = importlib.import_module("ahbo.agent.intent")
        fn = getattr(mod, "tag", None)
    except ImportError:
        fn = None
    if fn is not None:
        try:
            r = fn(text)
            if inspect.isawaitable(r):
                r = await r
            intent = r.get("intent") if isinstance(r, dict) else r
            if intent in INTENTS:
                return intent
        except Exception:
            log.warning("intent.tag failed; using keyword fallback", exc_info=True)
    return keyword_intent(text)


# ---------------------------------------------------------------- counting
def count(db: Database, clock: Clock, intent: str) -> int:
    """repetition[today][intent] += 1; remembers the last intent for the hint. Returns the new count."""
    if intent not in INTENTS:
        intent = "other"
    day = clock.today().isoformat()
    with db.transaction():
        db.execute("INSERT INTO repetition(date, intent, count) VALUES (?,?,1) "
                   "ON CONFLICT(date, intent) DO UPDATE SET count = count + 1", (day, intent))
        n = db.one("SELECT count FROM repetition WHERE date=? AND intent=?", (day, intent))["count"]
    db.kv_set(LAST_KEY, {"date": day, "intent": intent, "count": n, "ts": clock.iso()})
    return n


def get_count(db: Database, day: str, intent: str) -> int:
    r = db.one("SELECT count FROM repetition WHERE date=? AND intent=?", (day, intent))
    return int(r["count"]) if r else 0


def repetition_hint(db: Database, clock: Clock) -> str | None:
    """The §5.2 repetition block: a hint when her latest question is one she has asked ≥ 3 times today."""
    last = db.kv_get(LAST_KEY) or {}
    day = clock.today().isoformat()
    if last.get("date") != day or last.get("intent") in (None, "other"):
        return None
    n = get_count(db, day, last["intent"])
    if n < THRESHOLD:
        return None
    return HINT.format(n=nw.int_words(n))


# ---------------------------------------------------------------- the answer card (Kernel data only)
def _next_dose(db: Database, clock: Clock) -> dict[str, Any] | None:
    now = clock.now()
    hm = now.strftime("%H:%M")
    rows = db.query("SELECT s.slot_id, s.time, s.med_names, d.state FROM med_schedule s LEFT JOIN dose_events d "
                    "ON d.slot_id=s.slot_id AND d.date=? WHERE s.active=1 ORDER BY s.time", (now.date().isoformat(),))
    for r in rows:
        state = r["state"] or "scheduled"
        if state == TAKEN:
            continue
        if r["time"] >= hm or state in ("reminding", "snoozed"):
            return {**r, "state": state, "due_now": state in ("reminding", "snoozed") or r["time"] <= hm}
    return None


def answer_card(db: Database, clock: Clock, intent: str) -> dict[str, Any] | None:
    """The persistent info card for a repeated question, or None when there is nothing factual to show."""
    now = clock.now()
    card: dict[str, Any] = {"id": f"rep_{intent}", "kind": "info", "persistent": True}
    if intent == "ask_med_time":
        d = _next_dose(db, clock)
        if d is None:
            return {**card, "label": "今日啲藥食晒喇", "small": "聽日朝早再食"}
        cat = {r["id"]: r["yue"] for r in db.query("SELECT id, yue FROM med_catalogue")}
        meds = "、".join(cat[m] for m in loads(d["med_names"], []) if m in cat)
        if d["due_now"]:
            return {**card, "label": "而家要食藥", "small": meds or None}
        return {**card, "label": f"下次食藥：{nw.hhmm_words(d['time'])}",
                "small": f"{nw.card_time(d['time'])} · {meds}" if meds else nw.card_time(d["time"])}
    if intent == "ask_date":
        return {**card, "label": f"今日：{nw.int_words(now.month)}月{nw.int_words(now.day)}號",
                "small": WEEKDAYS[now.weekday()]}
    if intent == "ask_today":
        day = now.date().isoformat()
        evs = db.query("SELECT datetime, title FROM calendar_events WHERE substr(datetime,1,10)=? "
                       "AND status != 'cancelled' AND datetime >= ? ORDER BY datetime", (day, now.isoformat()[:16]))
        if not evs:
            return {**card, "label": "今日冇其他安排", "small": "可以慢慢嚟"}
        first = evs[0]
        hm = first["datetime"][11:16]
        rest = " · ".join(f"{nw.card_time(e['datetime'][11:16])} {e['title']}" for e in evs[1:3])
        return {**card, "label": f"下一樣：{nw.hhmm_words(hm)}{first['title']}", "small": rest or None}
    if intent == "ask_family_visit":
        names = [r["name"] for r in db.query("SELECT name FROM contacts WHERE role='family'")]
        if not names:
            return None
        until = (now + timedelta(days=14)).isoformat()
        for e in db.query("SELECT datetime, title FROM calendar_events WHERE status != 'cancelled' "
                          "AND datetime >= ? AND datetime <= ? ORDER BY datetime", (now.isoformat()[:16], until)):
            who = next((n for n in names if n in e["title"]), None)
            if who:
                dt = datetime.fromisoformat(e["datetime"])
                when = "今日" if dt.date() == now.date() else ("聽日" if dt.date() == now.date() + timedelta(days=1)
                                                             else WEEKDAYS[dt.weekday()])
                return {**card, "label": f"{who}：{when}{nw.time_words(dt.hour, dt.minute)}",
                        "small": e["title"]}
        return None      # nothing on the calendar: never invent a visit
    return None          # ask_where_item: object memory is not in v1 (D10)


# ---------------------------------------------------------------- digest trends
def trend_summary(db: Database, day: date | str, window_days: int = 7) -> dict[str, Any]:
    """Counts for `day` vs the average over the previous `window_days` days she talked (no exact words, §7.4)."""
    d = date.fromisoformat(day) if isinstance(day, str) else day
    start = (d - timedelta(days=window_days)).isoformat()
    today_rows = {r["intent"]: r["count"] for r in db.query("SELECT intent, count FROM repetition WHERE date=?",
                                                            (d.isoformat(),))}
    hist = db.query("SELECT date, intent, count FROM repetition WHERE date >= ? AND date < ?", (start, d.isoformat()))
    days = {r["date"] for r in hist}
    items = []
    for intent in INTENTS:
        if intent == "other":
            continue
        n = today_rows.get(intent, 0)
        if n == 0:
            continue
        avg = (sum(r["count"] for r in hist if r["intent"] == intent) / len(days)) if days else None
        items.append({"intent": intent, "count": n, "avg": round(avg, 1) if avg is not None else None,
                      "notable": n >= THRESHOLD})
    items.sort(key=lambda x: -x["count"])

    def fmt(x: float) -> str:
        return str(int(x)) if float(x).is_integer() else str(x)

    zh = [f"{LABEL_ZH[i['intent']]} {i['count']} 次" + (f"（平時平均 {fmt(i['avg'])} 次）" if i["avg"] is not None else "")
          for i in items]
    en = [f"{LABEL_EN[i['intent']]} {i['count']}×" + (f" (avg {fmt(i['avg'])}×)" if i["avg"] is not None else "")
          for i in items]
    return {"date": d.isoformat(), "items": items, "text": "；".join(zh), "text_en": "; ".join(en)}
