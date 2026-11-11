"""Plain helpers for the bot tests (fake Telegram updates; no network)."""
from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from typing import Any

from ahbo.core.jsonutil import loads
from ahbo.core.settings import load_settings
from ahbo.links import telegram

CHUN_CHAT = "8925008896"
STRANGER = "777000111"


def make_settings(tmp_path, **kw):
    os.environ.setdefault("AHBO_FAKE", "all")
    s = load_settings()
    base = dict(app_secret="test-app-secret-0123456789abcdef", internal_token="test-internal-token-0123456789",
                session_secret="test-session-secret-xyz", public_base_url="https://test.invalid",
                telegram_webhook_secret="tg-secret", twilio_auth_token="test-twilio-authtoken",
                twilio_account_sid="ACtest0000000000000000000000000000", twilio_from="+85230000000",
                family_telegram_chat_ids={"阿珍": CHUN_CHAT}, family_phone="+85291230000")
    base.update(kw)
    return replace(s, data_dir=tmp_path, **base)


_uid = [100]


def text_update(chat: str, text: str) -> dict[str, Any]:
    _uid[0] += 1
    return {"update_id": _uid[0], "message": {"message_id": _uid[0], "date": 0, "text": text,
                                              "chat": {"id": int(chat), "type": "private"},
                                              "from": {"id": int(chat), "first_name": "x"}}}


def cb_update(chat: str, data: str) -> dict[str, Any]:
    _uid[0] += 1
    return {"update_id": _uid[0], "callback_query": {"id": f"cbq{_uid[0]}", "from": {"id": int(chat)}, "data": data,
                                                     "message": {"chat": {"id": int(chat)}, "message_id": 1}}}


def channel(svc: Any) -> Any:
    return svc.extra["telegram_channel"]


async def say(svc: Any, text: str, chat: str = CHUN_CHAT) -> list[dict[str, Any]]:
    """Send a typed message; returns the replies the channel delivered for it."""
    ch = channel(svc)
    n = len(ch.replies)
    await telegram.handle_update(svc, text_update(chat, text))
    return ch.replies[n:]


async def tap(svc: Any, data: str, chat: str = CHUN_CHAT) -> list[dict[str, Any]]:
    ch = channel(svc)
    n = len(ch.replies)
    await telegram.handle_update(svc, cb_update(chat, data))
    return ch.replies[n:]


def labels(reply: dict[str, Any]) -> dict[str, str]:
    return {b["text"]: b["callback_data"] for row in reply["buttons"] for b in row}


def button(reply: dict[str, Any], startswith: str) -> str:
    for text, data in labels(reply).items():
        if text.startswith(startswith):
            return data
    raise AssertionError(f"no button {startswith!r} in {list(labels(reply))}")


async def confirm(svc: Any, replies: list[dict[str, Any]], ok: bool = True) -> list[dict[str, Any]]:
    last = replies[-1]
    assert "確認？" in last["text"], last["text"]
    return await tap(svc, button(last, "✅" if ok else "❌"))


def events_of(svc: Any, type_: str) -> list[dict[str, Any]]:
    rows = svc.db.query("SELECT * FROM events WHERE type=? ORDER BY rowid", (type_,))
    for r in rows:
        r["payload"] = loads(r["payload"], {})
    return rows


def commands_of(svc: Any, type_: str) -> list[dict[str, Any]]:
    rows = svc.db.query("SELECT * FROM commands WHERE type=? ORDER BY rowid", (type_,))
    for r in rows:
        r["payload"] = loads(r["payload"], {})
    return rows


async def bot_tasks(svc: Any) -> None:
    tasks = list(svc.extra.get("bot_tasks") or [])
    if tasks:
        await asyncio.wait_for(asyncio.gather(*tasks), 10)


async def settle(svc: Any, seconds: float = 0.3) -> None:
    for _ in range(100):
        if svc.kernel.queue.empty():
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(seconds)
