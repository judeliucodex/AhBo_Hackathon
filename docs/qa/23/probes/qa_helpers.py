"""QA 23 shared helpers: the real app services in fake mode, driven via telegram.handle_update.

Mirrors brain/tests/bot/conftest.py (read from the repo, not modified). Nothing here touches the network:
replies land in svc.extra["telegram_channel"].replies (FakeTelegram).
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any

sys.path.insert(0, "/repo/brain/tests")
sys.path.insert(0, "/repo/brain/tests/bot")

import bot_helpers  # noqa: E402  (repo read-only; helpers only)
from bot_helpers import CHUN_CHAT, STRANGER, make_settings  # noqa: E402,F401

from ahbo.api.services import build_services  # noqa: E402
from ahbo.core.clock import FakeClock  # noqa: E402
from ahbo.core.jsonutil import loads  # noqa: E402
from ahbo.links import telegram  # noqa: E402

# never let the fixed-clip renderer write the iOS bundle into the (read-only) repo
from ahbo.ops import render_fixed  # noqa: E402
render_fixed._copy_ios_bundle = lambda *a, **k: None

# ---- Traditional/Simplified check (opencc s2t: a change means a Simplified-only char was present)
import opencc  # noqa: E402
_s2t = opencc.OpenCC("s2t")


def simplified_hits(text: str) -> list[str]:
    """Characters that OpenCC s2t would change = Simplified-only characters (INV-10 wants Traditional)."""
    conv = _s2t.convert(text)
    return sorted({c for c, t in zip(text, conv) if c != t})


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


async def make_svc_fixture(tmp_path, made: list, db_path: str = ":memory:",
                           start: str = "2026-11-20T15:00:00+08:00", **kw):
    """Build the real services (Kernel + links wiring + Device Hub) with fake channels."""
    svc = build_services(make_settings(tmp_path, **kw), db_path=db_path, clock=FakeClock(start), time_scale=0.05)
    await svc.start()
    svc.db.execute("UPDATE contacts SET telegram_chat_id=? WHERE id='c_chun'", (CHUN_CHAT,))
    made.append(svc)
    return svc


async def stop_all(made: list) -> None:
    for svc in made:
        t = svc.extra.get("scenario_task")
        if t and not t.done():
            t.cancel()
        try:
            await svc.stop()
        except Exception:
            pass


async def say(svc: Any, text: str, chat: str = CHUN_CHAT) -> list[dict[str, Any]]:
    ch = bot_helpers.channel(svc)
    n = len(ch.replies)
    await telegram.handle_update(svc, bot_helpers.text_update(chat, text))
    return ch.replies[n:]


async def tap(svc: Any, data: str, chat: str = CHUN_CHAT) -> list[dict[str, Any]]:
    ch = bot_helpers.channel(svc)
    n = len(ch.replies)
    await telegram.handle_update(svc, bot_helpers.cb_update(chat, data))
    return ch.replies[n:]


async def confirm(svc: Any, replies: list[dict[str, Any]], ok: bool = True) -> list[dict[str, Any]]:
    last = replies[-1]
    assert "確認？" in last["text"], f"expected a 確認 prompt, got: {last['text']!r}"
    return await tap(svc, bot_helpers.button(last, "✅" if ok else "❌"))


async def settle(svc: Any, seconds: float = 0.3) -> None:
    for _ in range(100):
        if svc.kernel.queue.empty():
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(seconds)


async def bot_tasks(svc: Any) -> None:
    tasks = list(svc.extra.get("bot_tasks") or [])
    if tasks:
        await asyncio.wait_for(asyncio.gather(*tasks), 10)
