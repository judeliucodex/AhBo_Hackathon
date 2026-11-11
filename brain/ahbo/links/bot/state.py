"""Per-chat conversation state (kv `bot_convo:<chat_id>`), so a restart doesn't lose a half-done edit.

`{"form": {name, id, step, data, msg}|None, "pending": {op, args, summary, nonce}|None, "ts": epoch}`.
Expires 10 minutes after the last touch (real time, not the virtual clock: a demo clock jump must not expire or
revive an edit).
"""
from __future__ import annotations

import secrets
import time
from typing import Any

TTL_S = 600.0
PREFIX = "bot_convo:"


def _key(chat_id: str) -> str:
    return f"{PREFIX}{chat_id}"


def now() -> float:
    return time.time()


def load(db: Any, chat_id: str) -> dict[str, Any]:
    st = db.kv_get(_key(chat_id), None)
    if not isinstance(st, dict) or now() - float(st.get("ts") or 0) > TTL_S:
        return {"form": None, "pending": None}
    return st


def save(db: Any, chat_id: str, st: dict[str, Any]) -> None:
    st = {"form": st.get("form"), "pending": st.get("pending"), "ts": now()}
    db.kv_set(_key(chat_id), st)


def clear(db: Any, chat_id: str) -> None:
    db.kv_set(_key(chat_id), {"form": None, "pending": None, "ts": 0})


def new_nonce() -> str:
    return secrets.token_hex(4)
