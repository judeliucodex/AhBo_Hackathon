"""The `action.run` dispatcher (`actions/runner.py`, `wiring.py`): allowlist, cap, method dispatch,
`action.status` events, and the whole Kernel flow propose→confirm→run→done with a fake browser."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from ahbo.actions import Outcome, runner, wiring
from ahbo.actions.playwright_form import Job
from ahbo.core.events import Command, Event
from ahbo.trace.costs import record_cost

PUBLIC = "https://test.invalid"      # tests/conftest.py pins PUBLIC_BASE_URL
SHOTS = Path(tempfile.gettempdir()) / "ahbo-actions-test-shots"


class Svc:
    """The slice of `Services` that wiring.install touches (kernel.spawn does the background work)."""

    def __init__(self, h):
        self.kernel = h.kernel
        self.db = h.kernel.db


async def propose_and_confirm(h, kind="signup", target="act_opera_1500", **details) -> str:
    r = await h.kernel.request(Event(type="action.propose_request", source="agent",
                                     payload={"kind": kind, "target": target, "details": details,
                                              "session_id": None}))
    assert r["ok"], r
    aid = r["action_id"]
    r2 = await h.kernel.request(Event(type="action.confirm_request", source="agent",
                                      payload={"action_id": aid, "utterance": "好呀", "stt_confidence": 0.95}))
    assert r2["ok"] and r2["status"] == "running", r2
    return aid


async def settle(h, pred, timeout=5.0) -> bool:
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if pred():
            return True
        await asyncio.sleep(0.02)
    return False


def state_of(h, aid) -> str:
    return h.kernel.db.one("SELECT state FROM actions WHERE id=?", (aid,))["state"]


@pytest.fixture
def wired(harness, monkeypatch):
    calls: list[Job] = []

    async def fake_submit_form(job, settings, *, browser_ws=None):
        calls.append(job)
        SHOTS.mkdir(parents=True, exist_ok=True)
        pre, post = SHOTS / "pre_submit.png", SHOTS / "post_submit.png"
        pre.write_bytes(b"png")
        post.write_bytes(b"png")
        return Outcome("done", {"reason": "booked", "reference": "DEMO-0423",
                                "pre": str(pre), "post": str(post)}, [str(pre), str(post)])

    monkeypatch.setattr("ahbo.actions.playwright_form.submit_form", fake_submit_form)
    wiring.install(Svc(harness))
    h = harness
    h.events: list[dict] = []
    h.kernel.subscribe(lambda kind, obj: h.events.append(obj) if kind == "kernel_event" else None)
    h.cmds = []
    h.kernel.subscribe(lambda kind, obj: h.cmds.append(obj) if kind == "command" else None)
    h.browser_calls = calls
    return h


def statuses(h, aid) -> list[dict]:
    return [o["payload"] for o in h.events if o["type"] == "action.status"
            and o["payload"].get("action_id") == aid]


def sends_of(h, type_) -> list[dict]:
    return [o["payload"] for o in h.events if o["type"] == type_]


async def test_wiring_registers_dispatcher_and_table(wired):
    h = wired
    assert "action.run" in h.kernel.dispatchers
    assert h.kernel.db.one("SELECT name FROM sqlite_master WHERE name='mock_registrations'")
    from ahbo.actions import mock_centre
    assert wiring.router is mock_centre.router


async def test_signup_flow_done(wired):
    h = wired
    aid = await propose_and_confirm(h, activity_id="act_opera_1500")
    assert await settle(h, lambda: state_of(h, aid) == "done")
    st = statuses(h, aid)
    assert [s["status"] for s in st] == ["filling", "done"]
    done = st[-1]
    assert done["detail"]["reference"] == "DEMO-0423"
    assert [p.endswith(("pre_submit.png", "post_submit.png")) for p in done["screenshots"]] == [True, True]
    # the Kernel turned the result into the family NOTIFY + booked calendar event (reducer, §4.3.5)
    send = [c for c in h.cmds if c["type"] == "family.send" and c["payload"].get("action_id") == aid]
    assert send and "DEMO-0423" in send[0]["payload"]["text"]
    assert h.kernel.db.one("SELECT status FROM calendar_events WHERE action_id=?", (aid,))["status"] == "booked"
    job = h.browser_calls[0]
    assert job.url.startswith(f"{PUBLIC}/mock-centre/")                 # allowlisted public url
    assert job.navigate_to.startswith("http://api:8000/mock-centre/")  # internal rewrite, query kept
    assert job.choice.activity_id == "act_opera_1500" and job.choice.session_value == "15:00-16:30"
    assert job.choice.activity_title == "粵曲班"
    assert job.shareable["name_zh"] == "王婆婆"
    assert job.forbidden_terms and "身份證號碼" in job.forbidden_terms
    row = h.kernel.db.one("SELECT usd FROM costs WHERE provider='playwright'")
    assert row is not None and row["usd"] == 0.0                       # the cap counts the attempt


async def test_denied_and_read_only_domains_never_open_the_browser(wired):
    h = wired
    shareable = runner.shareable_of(h.kernel)
    o = await runner.run_playwright(h.kernel, "act_test1", {"url": "https://elsewhere.example/form"},
                                    shareable)
    assert o.status == "needs_human" and o.detail["reason"] == "domain_not_allowed"
    o = await runner.run_playwright(h.kernel, "act_test2", {"url": "https://www.gov.hk/activities/"},
                                    shareable)
    assert o.status == "needs_human" and o.detail["reason"] == "read_only_domain"
    o = await runner.run_playwright(h.kernel, "act_test3", {"activity_id": "act_taichi_0900"}, shareable)
    assert o.status == "failed" and o.detail["reason"] == "no_url"    # in-person seed has no web url
    assert h.browser_calls == []


async def test_daily_cap_disables_playwright(wired):
    h = wired
    for _ in range(10):                                                # §11: 10 Playwright actions/day
        record_cost(h.kernel.db, "playwright", None, {}, 0.0, clock=h.kernel.clock)
    aid = await propose_and_confirm(h, activity_id="act_opera_1500")
    assert await settle(h, lambda: state_of(h, aid) == "failed")
    st = statuses(h, aid)
    assert st[-1]["status"] == "failed" and st[-1]["detail"]["reason"] == "daily_cap"
    assert h.browser_calls == []


async def test_handoff_method_needs_human(wired):
    h = wired
    aid = await propose_and_confirm(h, target="act_taichi_0900", activity_id="act_taichi_0900")
    assert await settle(h, lambda: state_of(h, aid) == "needs_human")
    st = statuses(h, aid)
    assert st[-1]["detail"]["reason"] == "method_handoff"
    assert h.browser_calls == []


async def test_telegram_message_method(wired):
    h = wired
    aid = await propose_and_confirm(h, kind="message", target="c_chun", contact_id="c_chun",
                                    title="粵曲班", activity_id="act_opera_1500", time="15:00",
                                    venue="秀茂坪長者中心（示範）")
    assert await settle(h, lambda: state_of(h, aid) == "done")
    msgs = sends_of(h, "action.message_send")
    assert len(msgs) == 1
    assert msgs[0]["contact_id"] == "c_chun" and msgs[0]["template"] is True
    text = msgs[0]["text"]
    assert "王婆婆" in text and "阿珍" in text and "粵曲班" in text and "（示範號碼）" in text
    assert "http" not in text                                          # template + details only
    st = statuses(h, aid)
    assert st[-1]["status"] == "done" and st[-1]["detail"]["sent_via"] == "action.message_send"


async def test_telegram_to_unknown_contact_needs_human(wired):
    h = wired
    aid = await propose_and_confirm(h, kind="message", target="路人甲", contact_id="路人甲")
    assert await settle(h, lambda: state_of(h, aid) == "needs_human")
    st = statuses(h, aid)
    assert st[-1]["detail"]["reason"] == "contact_not_allowlisted"
    assert sends_of(h, "action.message_send") == []


async def test_runner_ignores_non_running_actions(wired):
    h = wired
    await runner.run_action(h.kernel, Command(type="action.run", payload={"action_id": "act_nope"}))
    await runner.run_action(h.kernel, Command(type="action.run", payload={"action_id": ""}))
    assert h.browser_calls == [] and not h.events
