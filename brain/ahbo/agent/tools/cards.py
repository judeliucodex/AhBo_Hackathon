"""show_card (§5.5): show one kit card on the face (sent by the Kernel with the reply, §23.7). Only the kit's
fields are kept; the Kernel's own card ids (incident, esc_*, today, next_dose, rep_*) and persistence are refused,
so a model can never fake or hide a safety card."""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from . import ToolCtx, tool

KINDS = ("med", "event", "plan", "voice_note", "action", "message", "story", "today", "photo", "activity", "info")
RESERVED = re.compile(r"^(incident|esc_|today$|next_dose$|rep_|action_|fall)")
URL = re.compile(r"https?://|www\.", re.I)


class Card(BaseModel):
    model_config = {"extra": "ignore"}
    id: str | None = Field(None, max_length=40)
    kind: Literal[KINDS]  # type: ignore[valid-type]
    label: str = Field(min_length=1, max_length=24)
    small: str | None = Field(None, max_length=30)
    icon: str | None = Field(None, max_length=4)


class ShowCardArgs(BaseModel):
    model_config = {"extra": "ignore"}
    card: Card


@tool("show_card", "喺塊面度顯示一張卡（例如一個地方、一個提醒），俾佢睇吓。", ShowCardArgs)
async def show_card(ctx: ToolCtx, args: ShowCardArgs) -> dict[str, Any]:
    c = args.card
    if c.kind == "today" or (c.id and RESERVED.match(c.id)):
        return {"ok": False, "reason": "reserved"}
    if URL.search(c.label + (c.small or "")):
        return {"ok": False, "reason": "bad_args"}
    from ahbo.core.ids import new_id
    card: dict[str, Any] = {"id": f"show_{c.id or new_id('c')[2:]}"[:48], "kind": c.kind, "label": c.label}
    if c.small:
        card["small"] = c.small
    if c.icon:
        card["icon"] = c.icon
    card["when_candidates"] = [c.label]
    return {"ok": True, "_cards": [card]}
