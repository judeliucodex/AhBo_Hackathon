"""Bot tests: the real app services (Kernel, links wiring with the *fake* Telegram/Twilio channels, Device Hub,
text loop) built in the test's own event loop, so `telegram.handle_update` can be awaited directly.

Nothing here sends a Telegram message, sets a webhook or calls any API: replies land in
`svc.extra["telegram_channel"].replies` (FakeTelegram), and the Twilio caller-ID lookup uses an
`httpx.MockTransport`. The fixed-clip renderer is stubbed so tests never write the iOS bundle in the repo.
"""
from __future__ import annotations

import pytest
from bot_helpers import CHUN_CHAT, make_settings

from ahbo.api.services import build_services
from ahbo.core.clock import FakeClock


@pytest.fixture(autouse=True)
def _no_repo_writes(monkeypatch):
    from ahbo.ops import render_fixed
    monkeypatch.setattr(render_fixed, "_copy_ios_bundle", lambda *a, **k: None)


@pytest.fixture
def rerender_calls(monkeypatch):
    calls: list[str] = []
    from ahbo.links.bot import changes

    def fake(svc):
        calls.append("render")
        return f"f-test{len(calls)}"
    monkeypatch.setattr(changes, "rerender", fake)
    return calls


@pytest.fixture
async def make_svc(tmp_path):
    made = []

    async def make(db_path: str = ":memory:", start: str = "2026-11-20T15:00:00+08:00", **kw):
        svc = build_services(make_settings(tmp_path, **kw), db_path=db_path, clock=FakeClock(start), time_scale=0.05)
        await svc.start()
        svc.db.execute("UPDATE contacts SET telegram_chat_id=? WHERE id='c_chun'", (CHUN_CHAT,))
        made.append(svc)
        return svc

    yield make
    for svc in made:
        t = svc.extra.get("scenario_task")
        if t and not t.done():
            t.cancel()
        try:
            await svc.stop()
        except Exception:
            pass


@pytest.fixture
async def svc(make_svc, rerender_calls):
    return await make_svc()
