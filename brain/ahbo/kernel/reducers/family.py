"""Messages from Po-po to her family (§5.5 `send_message_to_family`, INV-1, INV-9/§11).

The tool only *asks* with the request event `family.message_request` (payload documented in
`ahbo/agent/tools/family.py`: `contact_id`, `contact_name`, `kind`, `text`, `her_words`, `session_id`, `turn_id`).
The Kernel decides:
- the recipient must be an allow-listed family contact (`contacts.role='family'`, `verified=1`); anything else →
  `{ok:false, reason:"not_allowlisted"}`;
- the outgoing text is rebuilt here from the fixed template + her own words, quoted (the tool's `text` field is not
  trusted; free LLM text never goes out) → `family.send {kind:"INFO", contact_ids:[id], text}` (the links
  component writes the outbox row and applies the INFO rate limit) → reply `{ok:true, contact}`;
- `kind:"voice_reply"` (record her next utterance and send the audio) is not built in v1 →
  `{ok:false, reason:"voice_reply_unavailable"}`, so the agent tells her honestly.
"""
from __future__ import annotations

from typing import Any

from ahbo.core.events import P2, Command, Event, cmd

from ..common import note
from ..state import KernelState

SOURCES = ("agent", "operator")
TEMPLATE_FALLBACK = "媽媽（王婆婆）託阿寶話你知：「{words}」"
MAX_WORDS = 300


def _template() -> str:
    try:                                   # one source of truth: the tool module owns the wording
        from ahbo.agent.tools.family import TEMPLATE
        return TEMPLATE
    except Exception:
        return TEMPLATE_FALLBACK


def clean_words(text: Any) -> str:
    """Her words as a quote: whitespace collapsed, corner brackets neutralised so the quote cannot be closed early."""
    t = " ".join(str(text or "").split())
    return t.replace("「", "『").replace("」", "』")[:MAX_WORDS]


def allowlisted_family(ctx: Any, contact_id: Any, contact_name: Any) -> dict[str, Any] | None:
    for key in (contact_id, contact_name):
        k = str(key or "").strip()
        if not k:
            continue
        row = ctx.db.one("SELECT id, name FROM contacts WHERE role='family' AND verified=1 AND (id=? OR name=?) "
                         "ORDER BY priority, id LIMIT 1", (k, k))
        if row:
            return row
    return None


def _refuse(ctx: Any, ev: Event, reason: str, **extra: Any) -> None:
    note(ctx, "family.message_refused", {"reason": reason, **extra}, ev.correlation_id or ev.id)
    ctx.reply(ev, {"ok": False, "reason": reason, **extra})


def _message_request(state: KernelState, ev: Event, ctx: Any) -> list[Command] | None:
    p = ev.payload
    if ev.source not in SOURCES:
        _refuse(ctx, ev, "bad_source")
        return None
    kind = str(p.get("kind") or "text")
    if kind == "voice_reply":
        _refuse(ctx, ev, "voice_reply_unavailable")
        return None
    if kind != "text":
        _refuse(ctx, ev, "bad_args")
        return None
    contact = allowlisted_family(ctx, p.get("contact_id"), p.get("contact_name"))
    if contact is None:
        _refuse(ctx, ev, "not_allowlisted")
        return None
    words = clean_words(p.get("her_words"))
    if not words:
        _refuse(ctx, ev, "bad_args", contact=contact["name"])
        return None
    text = _template().format(words=words)
    corr = ev.correlation_id or ev.id
    ctx.reply(ev, {"ok": True, "contact": contact["name"], "contact_id": contact["id"]})
    return [cmd("family.send", P2, corr, kind="INFO", contact_ids=[contact["id"]], text=text,
                origin="resident_message", session_id=p.get("session_id"), turn_id=p.get("turn_id"))]


def reduce(state: KernelState, ev: Event, ctx: Any) -> list[Command] | None:
    if ev.type == "family.message_request":
        return _message_request(state, ev, ctx)
    return None
