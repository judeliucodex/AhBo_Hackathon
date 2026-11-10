"""Context assembly for Flash (§5.2): persona + rules, then the volatile blocks, ≤ ~2.8k tokens in total.

| block       | budget | source |
|-------------|--------|--------|
| persona     | ~600   | prompts/persona.md (§5.4 verbatim) + prompts/rules.md |
| now         | ~60    | virtual clock + weather line |
| profile     | ≤800   | `memory.store.profile_block(db, 800)` |
| today       | ≤500   | calendar, day-plan summary, med status (Kernel dose state, INV-4) |
| episodic    | ≤500   | `memory.store.episodic_block(db, clock, 3, 500)` |
| pending     | ≤200   | unplayed voice notes, finished deep results, actions awaiting her |
| repetition  | ≤100   | `memory.repetition.repetition_hint(db, clock)` |

`build_instructions(kernel, session_id)` is what the text loop's TurnAgent uses every turn; `build_blocks` gives the
blocks separately for `GET /internal/context/{session_id}` (the LiveKit worker diffs them per turn).
Every block is plain data written by us; nothing in it is an instruction from a third party (INV-7): family texts,
web results and deep results never appear here except as quoted, validated summaries.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from ahbo.core import numwords as nw
from ahbo.core.jsonutil import loads

from . import prompts

log = logging.getLogger("ahbo.agent.context")

WEEKDAYS = "一二三四五六日"
BUDGETS = {"profile": 800, "today": 500, "episodic": 500, "pending": 200, "repetition": 100, "now": 80}
TOTAL_BUDGET = 2800
TAKEN = "taken_self_reported"
MODE_LINES = {
    "reminder": "而家係提醒時間：先講提醒嘅內容，再問佢食咗未。",
    "reassure": "而家係安撫模式：平靜、簡短；只可以用 get_incident_status 同 get_time；唔好承諾系統未確認嘅嘢。",
}


def estimate_tokens(text: str) -> int:
    try:
        from ahbo.memory.store import estimate_tokens as est
        return est(text)
    except Exception:  # memory not importable: one token per character is conservative for Chinese
        return len(text or "")


def trim(text: str, budget: int) -> str:
    """Keep whole lines while within the token budget."""
    if estimate_tokens(text) <= budget:
        return text
    out, used = [], 0
    for line in text.splitlines():
        c = estimate_tokens(line) + 1
        if used + c > budget:
            break
        out.append(line)
        used += c
    return "\n".join(out)


# ---------------------------------------------------------------- blocks
def now_block(kernel: Any) -> str:
    now = kernel.clock.now()
    line = (f"現在：{now.date().isoformat()}（{nw.int_words(now.month)}月{nw.int_words(now.day)}號，"
            f"星期{WEEKDAYS[now.weekday()]}），{nw.time_words(now.hour, now.minute, period=True)}（香港時間）。")
    w = weather_line(kernel)
    return line + (f"天氣：{w}。" if w else "")


def weather_line(kernel: Any) -> str:
    """Only what is already known (fake, or the 10-minute cache): context assembly never waits for the network."""
    from .tools import weather
    try:
        if kernel.settings.is_fake("models"):
            w = weather.fake_weather()
        else:
            hit = weather._cache.get("now")
            w = hit[1] if hit else None
        if not w:
            return ""
        return "，".join(x for x in (w.get("now_words"), w.get("forecast_words")) if x)
    except Exception:
        return ""


def profile_block(kernel: Any) -> str:
    try:
        from ahbo.memory import store
        return store.profile_block(kernel.db, BUDGETS["profile"])
    except Exception:
        log.exception("profile block failed")
        return ""


def episodic_block(kernel: Any) -> str:
    try:
        from ahbo.memory import store
        return store.episodic_block(kernel.db, kernel.clock, 3, BUDGETS["episodic"])
    except Exception:
        log.exception("episodic block failed")
        return ""


def repetition_block(kernel: Any) -> str:
    try:
        from ahbo.memory import repetition
        return repetition.repetition_hint(kernel.db, kernel.clock) or ""
    except Exception:
        log.exception("repetition hint failed")
        return ""


def _med_lines(kernel: Any) -> list[str]:
    db, now = kernel.db, kernel.clock.now()
    day, hm = now.date().isoformat(), now.strftime("%H:%M")
    cat = {r["id"]: r["yue"] for r in db.query("SELECT id, yue FROM med_catalogue")}
    rows = db.query("SELECT s.slot_id, s.time, s.med_names, d.state FROM med_schedule s LEFT JOIN dose_events d "
                    "ON d.slot_id=s.slot_id AND d.date=? WHERE s.active=1 ORDER BY s.time", (day,))
    parts = []
    for r in rows:
        h, m = (int(x) for x in r["time"].split(":"))
        names = "、".join(cat.get(x, x) for x in loads(r["med_names"], []))
        state = r["state"] or ("未到" if r["time"] > hm else "未記錄")
        state = {TAKEN: "佢話食咗", "reminding": "提醒緊，未話食咗", "snoozed": "延遲咗，未食",
                 "missed": "錯過咗，未話食咗"}.get(state, state)
        parts.append(f"{nw.time_words(h, m, period=True)}（{names}）：{state}")
    if not parts:
        return []
    return ["食藥（只可以用 get_med_status 答藥嘅問題）：" + "；".join(parts) + "。"]


def today_block(kernel: Any) -> str:
    db, now = kernel.db, kernel.clock.now()
    lines = []
    evs = db.query("SELECT datetime, title, place, kind FROM calendar_events WHERE substr(datetime,1,10)=? "
                   "AND status!='cancelled' ORDER BY datetime", (now.date().isoformat(),))
    if evs:
        items = []
        for e in evs:
            dt = datetime.fromisoformat(e["datetime"])
            items.append(f"{nw.time_words(dt.hour, dt.minute, period=True)}{e['title']}"
                         + (f"（{e['place']}）" if e.get("place") else ""))
        lines.append("今日日程：" + "；".join(items) + "。")
    else:
        lines.append("今日日程：冇約會。")
    plan = db.one("SELECT summary FROM day_plans WHERE date=?", (now.date().isoformat(),))
    if plan and plan.get("summary"):
        lines.append("今日計劃（朝早準備好）：" + str(plan["summary"])[:200])
    lines += _med_lines(kernel)
    return "\n".join(lines)


def pending_block(kernel: Any) -> str:
    db, now = kernel.db, kernel.clock.now()
    lines = []
    since = (now - timedelta(days=2)).isoformat()
    vms = db.query("SELECT from_name FROM voice_messages WHERE direction='in' AND played_at IS NULL AND received_at>=? "
                   "ORDER BY received_at DESC", (since,))
    if vms:
        lines.append(f"有{nw.int_words(len(vms), counting=True)}段未聽嘅家人錄音（{vms[0]['from_name'] or '家人'}）。")
    for d in db.query("SELECT result FROM deep_tasks WHERE status='done' AND delivered=0 ORDER BY finished_at DESC LIMIT 2"):
        r = loads(d.get("result"), {}) or {}
        if r.get("spoken_summary"):
            lines.append(f"背景搵好咗（未講俾佢聽）：「{str(r['spoken_summary'])[:80]}」")
    for a in db.query("SELECT question FROM actions WHERE state IN ('proposed','PROPOSED') ORDER BY created_at DESC LIMIT 1"):
        if a.get("question"):
            lines.append(f"等緊佢答：「{str(a['question'])[:60]}」")
    return "\n".join(lines)


def build_blocks(kernel: Any, session_id: str | None = None) -> dict[str, str]:
    blocks: dict[str, str] = {}
    for name, fn in (("now", now_block), ("profile", profile_block), ("today", today_block),
                     ("episodic", episodic_block), ("pending", pending_block), ("repetition", repetition_block)):
        try:
            blocks[name] = trim(fn(kernel) or "", BUDGETS[name])
        except Exception:
            log.exception("context block %s failed", name)
            blocks[name] = ""
    return blocks


HEADINGS = {"now": "【而家】", "profile": "【王婆婆嘅資料】", "today": "【今日】", "episodic": "【最近傾過】",
            "pending": "【未講嘅事】", "repetition": "【留意】"}


def assemble(blocks: dict[str, str], mode: str = "companion") -> str:
    parts = [prompts.persona(), prompts.rules()]
    if mode in MODE_LINES:
        parts.append(MODE_LINES[mode])
    parts += [f"{HEADINGS[n]}\n{blocks[n]}" for n in ("now", "today", "pending", "repetition", "profile", "episodic")
              if blocks.get(n)]
    text = "\n\n".join(parts)
    # hard cap: halve the lowest-value blocks first while the total is still too large
    for name in ("episodic", "profile", "pending"):
        if estimate_tokens(text) <= TOTAL_BUDGET:
            break
        text = assemble({**blocks, name: trim(blocks.get(name, ""), BUDGETS[name] // 2)}, mode) \
            if blocks.get(name) and estimate_tokens(blocks[name]) > BUDGETS[name] // 2 else text
    return text


def current_mode(kernel: Any) -> str:
    s = kernel.state.session
    return s.mode if s else "companion"


def build_instructions(kernel: Any, session_id: str | None = None) -> str:
    """§5.2: persona (verbatim) + rules + the Kernel/memory blocks, ≤ ~2.8k tokens."""
    return assemble(build_blocks(kernel, session_id), current_mode(kernel))


def context_payload(kernel: Any, session_id: str | None) -> dict[str, Any]:
    """`GET /internal/context/{session_id}` body (pending injections are added by the wiring's pull queue)."""
    blocks = build_blocks(kernel, session_id)
    mode = current_mode(kernel)
    return {"ok": True, "mode": mode, "instructions": assemble(blocks, mode), "blocks": blocks,
            "pending_injections": []}
