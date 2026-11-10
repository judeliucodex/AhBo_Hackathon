"""send_message_to_family (§5.5): a text (template + her words, quoted) or a voice reply to an allow-listed family
contact. The tool only *asks*: Kernel event `family.message_request` (reply `{ok, contact}`); the Kernel decides
and emits `family.send`. Contacts outside `contacts` (role family, verified) are refused here already (INV-9/§11).

`family.message_request` payload (source agent, correlation = session):
  `contact_id`, `contact_name`, `kind` (`text` | `voice_reply`), `text` (the full message for kind text, already
  templated: 「媽媽（王婆婆）託阿寶話你知：「…」」), `her_words` (her words alone), `session_id`, `turn_id`.
  `voice_reply`: the Kernel records her next utterance's audio (P1) and sends it as a voice note.
Reply: `{ok:true, contact}` / `{ok:false, reason}`.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from . import UNAVAILABLE, ToolCtx, kernel_request, tool

TEMPLATE = "媽媽（王婆婆）託阿寶話你知：「{words}」"


class SendArgs(BaseModel):
    model_config = {"extra": "ignore"}
    contact: str = Field("阿珍", max_length=20)
    kind: Literal["text", "voice_reply"] = "text"
    text: str | None = Field(None, max_length=300)


def find_family_contact(ctx: ToolCtx, name_or_id: str) -> dict[str, Any] | None:
    key = (name_or_id or "").strip()
    if not key:
        return None
    return ctx.db.one("SELECT id, name FROM contacts WHERE role='family' AND verified=1 AND (id=? OR name=?) "
                      "ORDER BY priority LIMIT 1", (key, key))


def _clean_words(text: str) -> str:
    t = " ".join((text or "").split())
    return t.replace("「", "『").replace("」", "』")[:300]


@tool("send_message_to_family", "幫佢傳訊息俾家人（例如阿珍）。要先問清楚佢想唔想傳、傳咩，佢話好先用。", SendArgs)
async def send_message_to_family(ctx: ToolCtx, args: SendArgs) -> dict[str, Any]:
    c = find_family_contact(ctx, args.contact)
    if c is None:
        return {"ok": False, "reason": "unknown_contact", "_step": {"outcome": "failed", "vars": {"contact": args.contact}}}
    words = _clean_words(args.text or "")
    if args.kind == "text" and not words:
        return {"ok": False, "reason": "bad_args", "_step": {"outcome": "failed", "vars": {"contact": c["name"]}}}
    res = await kernel_request(ctx, "family.message_request", contact_id=c["id"], contact_name=c["name"],
                               kind=args.kind, text=TEMPLATE.format(words=words) if words else "", her_words=words)
    if res is None:
        res = dict(UNAVAILABLE)
    res.setdefault("contact", c["name"])
    ok = bool(res.get("ok"))
    return {**res, "_step": {"outcome": "ok" if ok else "failed", "vars": {"contact": c["name"]}}}
