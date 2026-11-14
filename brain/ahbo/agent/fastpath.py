"""Demo fast path (13 Nov): the two scripted questions are answered with zero model calls.

「我幾時食藥呀？」 and 「我今日有咩安排呀？」 (and their STT variants) match here first — the turn runs the
same read-only Kernel tool as the normal path (so the cards/steps on the face are identical), then speaks a
canned phrasing built from the result. No thinking, no tool-round variance, no wording drift: the answer is
deterministic. Anything that doesn't match falls through to the normal LLM path untouched.
"""
from __future__ import annotations

import re
from typing import Any

_MED_Q = re.compile(r"(幾時|幾點|几点|几时|邊個位|咩時候|咩嘢時候|what time).{0,8}(食|吃|服).{0,4}(藥|药)"
                    r"|(食|吃)(咩|乜嘢|什麼|什么)(藥|药)")
_PLAN_Q = re.compile(r"(咩安排|咩嘢安排|什麼安排|什么安排|咩行程|日程|做咩|有咩搞作)|"
                     r"(今日|聽日|明天|明天).{0,6}(安排|日程|行程)")
_KINDS = ("med", "plan")
_TOOLS = {"med": ("get_med_status", {"scope": "next"}), "plan": ("get_day_plan", {})}


def match(text: str | None) -> str | None:
    """→ "med" | "plan" | None. Question-shaped utterances only (幾時/咩/what), not statements."""
    t = (text or "").strip()
    if not t:
        return None
    if _MED_Q.search(t):
        return "med"
    if _PLAN_Q.search(t):
        return "plan"
    return None


def tool_for(kind: str) -> tuple[str, dict[str, Any]]:
    return _TOOLS[kind]


def answer(kind: str, res: dict[str, Any] | None, text: str = "") -> str | None:
    """Canned Cantonese from the tool result; None → fall back to the normal LLM path."""
    if not isinstance(res, dict) or not res.get("ok"):
        return None
    if kind == "med":
        nxt = res.get("next")
        if not nxt:
            return None
        names = [m.get("yue") for m in nxt.get("meds", []) if m.get("yue")]
        names_s = names[0] if len(names) == 1 else ("、".join(names[:-1]) + "，同埋" + names[-1] if names else "你嘅藥")
        if nxt.get("due_now"):
            return f"而家夠鐘食藥喇：{names_s}。食完話我知。"
        tw = nxt.get("time_words") or nxt.get("time") or ""
        return f"{tw}。有{names_s}。到時我會提你㗎。"
    if kind == "plan":
        summary = (res.get("summary") or "").strip()
        if not summary:
            return None
        body = summary if summary.endswith(("。", "！", "？")) else f"{summary}。"
        tomorrow = any(w in (text or "") for w in ("聽日", "明天", "聽朝", "聽日朝早"))
        return f"聽日{body}" if tomorrow and not body.startswith("聽日") else body
    return None
