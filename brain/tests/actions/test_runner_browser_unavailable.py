"""No browser container (evals, tests, a stopped `browser` service): the sign-up is handed to 阿珍 (`needs_human`,
reason `browser_unavailable`) instead of a hard failure. Allowlist/forbidden-field checks run first and are
unchanged; a browser error while the browser IS reachable stays `failed`."""
from __future__ import annotations

import asyncio

import pytest

from ahbo.actions import Outcome, runner, wiring
from ahbo.core.events import Event


class Svc:
    def __init__(self, h):
        self.kernel = h.kernel
        self.db = h.kernel.db


@pytest.fixture
def wired(harness, monkeypatch):
    calls = []

    async def browser_down(job, settings, *, browser_ws=None):
        calls.append(job)
        return Outcome("failed", {"reason": "browser_error",
                                  "error": "Error: getaddrinfo ENOTFOUND browser"}, [])

    monkeypatch.setattr("ahbo.actions.playwright_form.submit_form", browser_down)
    wiring.install(Svc(harness))
    h = harness
    h.cmds, h.events = [], []
    h.kernel.subscribe(lambda kind, obj: h.cmds.append(obj) if kind == "command" else None)
    h.kernel.subscribe(lambda kind, obj: h.events.append(obj) if kind == "kernel_event" else None)
    h.browser_calls = calls
    return h


async def _run_signup(h, **details) -> str:
    r = await h.kernel.request(Event(type="action.propose_request", source="agent",
                                     payload={"kind": "signup", "target": "act_opera_1500", "details": details,
                                              "session_id": None}))
    aid = r["action_id"]
    r2 = await h.kernel.request(Event(type="action.confirm_request", source="agent",
                                      payload={"action_id": aid, "utterance": "好呀", "stt_confidence": 0.95}))
    assert r2["ok"], r2
    return aid


async def _until(pred, timeout=5.0) -> bool:
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if pred():
            return True
        await asyncio.sleep(0.02)
    return pred()


def _state(h, aid):
    return h.kernel.db.one("SELECT state FROM actions WHERE id=?", (aid,))["state"]


async def test_unreachable_browser_hands_off_to_ah_chun(wired, monkeypatch):
    h = wired

    async def unreachable(ws, timeout=2.0):
        return False

    monkeypatch.setattr(runner, "browser_reachable", unreachable)
    aid = await _run_signup(h, activity_id="act_opera_1500")
    assert await _until(lambda: _state(h, aid) == "needs_human")
    st = [e["payload"] for e in h.events if e["type"] == "action.status" and e["payload"]["action_id"] == aid]
    assert [s["status"] for s in st] == ["filling", "needs_human"]
    assert st[-1]["detail"]["reason"] == "browser_unavailable"
    hand = [c["payload"] for c in h.cmds if c["type"] == "family.send" and c["payload"].get("action_id") == aid]
    assert hand and hand[0]["kind"] == "HANDOFF" and "/mock-centre/" in hand[0]["text"]   # 阿珍 gets the link
    assert not h.kernel.db.one("SELECT id FROM costs WHERE provider='playwright'")   # nothing ran: no cap use


async def test_browser_error_with_reachable_browser_still_fails(wired, monkeypatch):
    h = wired

    async def reachable(ws, timeout=2.0):
        return True

    monkeypatch.setattr(runner, "browser_reachable", reachable)
    aid = await _run_signup(h, activity_id="act_opera_1500")
    assert await _until(lambda: _state(h, aid) == "failed")


async def test_domain_checks_still_run_before_the_browser(wired, monkeypatch):
    h = wired

    async def unreachable(ws, timeout=2.0):
        return False

    monkeypatch.setattr(runner, "browser_reachable", unreachable)
    aid = await _run_signup(h, activity_id="act_opera_1500", url="https://evil.example.com/form")
    assert await _until(lambda: _state(h, aid) == "needs_human")
    st = [e["payload"] for e in h.events if e["type"] == "action.status" and e["payload"]["action_id"] == aid]
    assert st[-1]["detail"]["reason"] == "domain_not_allowed"
    assert h.browser_calls == []


async def test_browser_reachable_probe():
    assert await runner.browser_reachable("ws://127.0.0.1:1/", timeout=1.0) is False
    assert await runner.browser_reachable("ws://no-such-host.invalid:3000/", timeout=2.0) is False
    assert await runner.browser_reachable("", timeout=1.0) is False
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        assert await runner.browser_reachable(f"ws://127.0.0.1:{port}/", timeout=2.0) is True
    finally:
        server.close()
        await server.wait_closed()
