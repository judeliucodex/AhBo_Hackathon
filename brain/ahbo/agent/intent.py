"""Intent tagging for repetition-aware replies (§7.4). `tag(text)` returns one intent from a closed set.

Real models: a cheap Flash call (`purpose="intent_tag"`, thinking off, JSON `{"intent": …}`), with a 3 s timeout;
anything outside the closed set, a timeout or an error falls back to the deterministic keyword tagger in
`ahbo.memory.repetition`. Fake models: the keyword tagger directly (deterministic). `ahbo.memory.repetition.tag_intent`
calls this module when it exists.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

log = logging.getLogger("ahbo.agent.intent")

INTENTS = ("ask_med_time", "ask_today", "ask_date", "ask_family_visit", "ask_where_item", "other")
TIMEOUT_S = 3.0
PROMPT = ("Classify the elderly Cantonese speaker's utterance into exactly one intent and reply with JSON only, "
          'like {"intent": "ask_today"}. Intents: ask_med_time (when/what medicine), ask_today (today\'s plan or '
          "appointments), ask_date (date or weekday), ask_family_visit (when family visits), ask_where_item "
          "(where she put something), other.")


def keyword(text: str) -> str:
    from ahbo.memory.repetition import keyword_intent
    return keyword_intent(text)


def parse(raw: str | None) -> str | None:
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        val = json.loads(m.group(0) if m else raw).get("intent")
    except (ValueError, AttributeError):
        val = raw.strip().strip('"')
    return val if val in INTENTS else None


async def tag(text: str, *, flash=None) -> str:
    text = (text or "").strip()
    if not text or text.startswith("[阿寶系統｜"):
        return "other"
    from ahbo.core.settings import get_settings
    if flash is None:
        if get_settings().is_fake("models"):
            return keyword(text)
        from ahbo.models import get_flash
        flash = get_flash()
    try:
        resp = await asyncio.wait_for(flash.chat([{"role": "system", "content": PROMPT},
                                                  {"role": "user", "content": text}], None, purpose="intent_tag"),
                                      TIMEOUT_S)
        return parse(resp.text) or keyword(text)
    except Exception as e:
        log.warning("intent tag failed (%r); keyword fallback", e)
        return keyword(text)
