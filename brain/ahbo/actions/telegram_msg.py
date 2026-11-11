"""The `telegram_message` action method (§4.3.5 Telegram-message method).

A sign-up by Telegram message (e.g. to a centre social worker) is a fixed template plus the approved action's
event details (title, time, venue) and the resident's shareable names/phone — **never** free LLM text, and
**never** the page's or anyone else's words (INV-7). The recipient must be on the `contacts` allowlist
(`verified`); anything else is `needs_human`, and 阿珍 gets the HANDOFF through the normal action flow.

The runner never talks to Telegram itself (INV-1: tools/components only request; the Kernel decides). It
validates the recipient, builds the text, then asks the Kernel to send by submitting the Kernel event

    action.message_send  {action_id, contact_id, contact_name, text, template: true}   (source `action`)

A reducer turns that into a `family.send` command (CONTRACTS §4.2), which is what actually goes out through
the links component with a fresh allowlist check. **Event name documented here**; until the reducer handles
it the event is still recorded in the trace (INV-6) and no message leaves the system.
"""
from __future__ import annotations

import re
from typing import Any

from ahbo.core.events import Event
from ahbo.core.numwords import hhmm_words

from . import Outcome

EVENT = "action.message_send"

SIGNUP_TEMPLATE = ("你好，呢度係{contact}。呢邊係阿寶（示範）。{name_zh}（{name_en}）想報名{title}，"
                   "時間：{when}，地點：{venue}。麻煩你幫手處理，聯絡電話：{phone}。多謝！")
MESSAGE_TEMPLATE = ("你好，呢度係{contact}。呢邊係阿寶（示範），替{name_zh}聯絡你。{context}"
                    "聯絡電話：{phone}。多謝！")


def _when(details: dict[str, Any]) -> str:
    when = str(details.get("time") or "")
    if re.fullmatch(r"\d{1,2}:\d{2}", when):
        when = hhmm_words(when, period=True) or when
    return when or "（待確認）"


def build_text(details: dict[str, Any], shareable: dict[str, Any], contact: str) -> str:
    """Template + event details only. Every placeholder comes from the approved action or the seed profile."""
    kw = {"contact": contact,
          "name_zh": str(shareable.get("name_zh") or "王婆婆"),
          "name_en": str(shareable.get("name_en") or ""),
          "phone": str(shareable.get("contact_phone") or "（示範號碼）")}
    if details.get("activity_id") or details.get("signup_method"):
        title = str(details.get("title") or details.get("activity_id") or "活動")
        venue = str(details.get("venue") or details.get("venue_short") or "（見活動資料）")
        return SIGNUP_TEMPLATE.format(title=title, when=_when(details), venue=venue, **kw)
    ctx = str(details.get("message_context") or "")
    if details.get("time"):
        ctx = f"事由：{details.get('title') or ''}，時間：{_when(details)}。" if details.get("title") \
            else f"時間：{_when(details)}。"
    return MESSAGE_TEMPLATE.format(context=ctx, **kw)


async def run(kernel: Any, action: dict[str, Any], details: dict[str, Any]) -> Outcome:
    """Validate the recipient against the contacts allowlist and ask the Kernel to send (see module doc)."""
    action_id = action["id"]
    target = str(details.get("contact_id") or details.get("contact") or action.get("target") or "").strip()
    row = kernel.db.one("SELECT id, name, verified FROM contacts WHERE id=? OR name=?", (target, target))
    if row is None or not row["verified"]:
        return Outcome("needs_human", {"reason": "contact_not_allowlisted", "target": target})
    text = build_text(details, action.get("shareable") or {}, str(row["name"]))
    await kernel.submit(Event(type=EVENT, source="action", correlation_id=action_id,
                              payload={"action_id": action_id, "contact_id": row["id"],
                                       "contact_name": row["name"], "text": text, "template": True}))
    return Outcome("done", {"method": "telegram_message", "contact_id": row["id"], "text": text,
                            "sent_via": EVENT})
