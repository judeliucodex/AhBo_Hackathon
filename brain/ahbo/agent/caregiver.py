"""Caregiver chat (13 Nov): free text from a verified family contact on Telegram → the agent answers.

The caregiver asks about Po-po ("媽媽今日食咗藥未？") and gets a Cantonese answer built from the live
Kernel state through the **read-only tools only** — the whitelist excludes anything that writes, acts or
messages, so a chat can never send a message to anyone, change a safety state, or touch a dose (INV-1/2/4
hold by construction). Replies go back to the caregiver's chat only; they are never spoken to Po-po.

History is memory-only (last few turns per chat, gone on restart) — caregiver chats are not persisted,
so the retention/PII rules for Po-po's data are untouched. A simple per-chat rate limit keeps a runaway
script from burning the LLM cap (`ops/caps.py` still owns the global budget).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

log = logging.getLogger("ahbo.agent.caregiver")

ALLOWED_TOOLS = ("get_med_status", "get_day_plan", "list_events", "get_incident_status",
                 "get_time", "get_weather")
MAX_ROUNDS = 4
HISTORY_TURNS = 6
RATE_MAX, RATE_WINDOW_S = 8, 60.0

SYSTEM = (
    "你係阿寶，屋企嘅 AI 助理。而家同緊阿珍（王婆婆嘅照顧者/女兒）喺 Telegram 傾偈。"
    "佢會問媽媽（王婆婆）嘅狀況。規矩：\n"
    "1. 用口語廣東話答，繁體字，最多三句，唔准用 markdown、emoji、英文。\n"
    "2. 藥、日程、事故資料一定要用工具查，唔可以靠記憶估。\n"
    "3. 永遠唔好俾醫療或用藥建議；藥物問題只可以報告工具嘅結果，提佢唔肯定就問醫生。\n"
    "4. 唔好承諾做任何嘢（你喺呢度冇手，只係答話）。有緊急事故就直接報告現況。\n"
    "5. 語氣親切，當佢係屋企人。"
)

_hist: dict[str, list[dict[str, Any]]] = {}
_rate: dict[str, list[float]] = {}


def _rate_ok(chat_id: str) -> bool:
    now = time.monotonic()
    win = [t for t in _rate.get(chat_id, []) if now - t < RATE_WINDOW_S]
    if len(win) >= RATE_MAX:
        _rate[chat_id] = win
        return False
    win.append(now)
    _rate[chat_id] = win
    return True


async def caregiver_reply(svc: Any, contact: dict[str, Any], chat_id: str, text: str) -> None:
    """Answer one caregiver message; delivers the reply through the Telegram channel. Never raises."""
    from ahbo.agent.tools import schemas
    from ahbo.agent.filters.spoken import spoken_filter
    from ahbo.models import get_flash

    channel = svc.extra.get("telegram_channel")
    if channel is None or not _rate_ok(chat_id):
        return
    bridge = getattr(svc, "bridge", None)
    if bridge is None:
        return
    turn_id = f"cg_{int(time.monotonic() * 1000)}"
    tools = [s for s in schemas("companion") if s.get("function", {}).get("name") in ALLOWED_TOOLS]
    hist = _hist.setdefault(chat_id, [])
    msgs: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM}]
    msgs += hist[-HISTORY_TURNS:]
    msgs.append({"role": "user", "content": text[:600]})
    try:
        for _ in range(MAX_ROUNDS):
            resp = await get_flash().chat(msgs, tools, purpose="caregiver_chat",
                                          correlation_id=turn_id)
            tcs = list(getattr(resp, "tool_calls", []) or [])
            if not tcs:
                reply = spoken_filter(resp.text or "").text
                break
            am: dict[str, Any] = {"role": "assistant", "content": resp.text or None, "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": json.dumps(tc.args, ensure_ascii=False)}}
                for tc in tcs]}
            if getattr(resp, "reasoning_content", None):
                am["reasoning_content"] = resp.reasoning_content
            msgs.append(am)
            for tc in tcs:
                if tc.name not in ALLOWED_TOOLS:          # defence in depth: run_tool re-checks modes too
                    res = {"ok": False, "reason": "not_allowed"}
                else:
                    res = await asyncio.wait_for(
                        bridge.call_tool(None, turn_id, tc.name, tc.args, tc.id), timeout=15.0)
                msgs.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(res, ensure_ascii=False)})
        else:
            reply = "而家資料多到亂，等陣再問我好嗎？"
    except Exception as e:                                 # never escape into the webhook handler
        log.warning("caregiver chat failed (%s)", type(e).__name__)
        reply = "阿寶而家諗唔到嘢，等陣再試吓好嗎？"
    if not reply:
        reply = "阿寶而家冇嘢補充。"
    hist.append({"role": "user", "content": text[:600]})
    hist.append({"role": "assistant", "content": reply})
    del _hist[chat_id][:-2 * HISTORY_TURNS]
    try:
        await channel.send(kind="INFO", contact_id=contact["id"], chat_id=str(chat_id), text=reply)
    except Exception as e:
        log.warning("caregiver reply send failed (%s)", type(e).__name__)
