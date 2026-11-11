"""Real-Playwright integration (§8.3, the task's integration step): the runner against the REAL mock site
served inside the `ahbo-t-actions` docker network (browser container + api container), with the public→
internal URL rewrite live.

Skipped unless AHBO_ACTION_E2E=1 — a plain `pytest -q tests/actions` has no browser network. The topology
is created and torn down by `e2e/run_e2e.sh` (see that file for the exact commands and its pasted output).
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest

from ahbo.core.events import Event

E2E = os.environ.get("AHBO_ACTION_E2E") == "1"
pytestmark = pytest.mark.skipif(
    not E2E, reason="needs the ahbo-t-actions docker network; run brain/tests/actions/e2e/run_e2e.sh")
if E2E:
    pytestmark = [pytestmark, pytest.mark.browser]


class Svc:
    """The slice of `Services` that wiring.install touches."""

    def __init__(self, harness):
        self.kernel = harness.kernel
        self.db = harness.kernel.db


async def settle(h, pred, timeout=90.0) -> bool:
    """Generous margin: the browser container runs on a small (2-vCPU) box and, under load, the first
    connect + page loads can take tens of seconds. The runner's own budget is 110 s (the Kernel's guard
    120 s), so 90 s stays inside both."""
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if pred():
            return True
        await asyncio.sleep(0.05)
    return False


def state_of(h, aid) -> str:
    return h.kernel.db.one("SELECT state FROM actions WHERE id=?", (aid,))["state"]


def statuses(h, aid) -> list[dict]:
    return [o["payload"] for o in h.events if o["type"] == "action.status"
            and o["payload"].get("action_id") == aid]


async def propose_set_url_confirm(h, url: str | None = None) -> str:
    """PROPOSED → (optionally pin the target url in the row) → APPROVED (which emits action.run)."""
    r = await h.kernel.request(Event(
        type="action.propose_request", source="agent",
        payload={"kind": "signup", "target": "act_opera_1500",
                 "details": {"activity_id": "act_opera_1500"}, "session_id": None}))
    assert r["ok"], r
    aid = r["action_id"]
    if url is not None:
        row = h.kernel.db.one("SELECT details FROM actions WHERE id=?", (aid,))
        d = json.loads(row["details"])
        d["url"] = url
        h.kernel.db.execute("UPDATE actions SET details=? WHERE id=?",
                            (json.dumps(d, ensure_ascii=False), aid))
    r2 = await h.kernel.request(Event(
        type="action.confirm_request", source="agent",
        payload={"action_id": aid, "utterance": "好呀", "stt_confidence": 0.95}))
    assert r2["ok"], r2
    return aid


@pytest.fixture
def wired(harness):
    from ahbo.actions import wiring
    wiring.install(Svc(harness))
    harness.events = []
    harness.kernel.subscribe(lambda kind, obj: harness.events.append(obj)
                             if kind == "kernel_event" else None)
    return harness


async def test_mock_site_renders_over_the_internal_url(harness):
    """Real Chromium in the browser container fetching http://api:8000/mock-centre/ (the rewritten URL)."""
    from playwright.async_api import async_playwright
    nav = "http://api:8000/mock-centre/"
    async with async_playwright() as pw:
        browser = await pw.chromium.connect(harness.kernel.settings.browser_ws, timeout=10000)
        page = await (await browser.new_context(accept_downloads=False)).new_page()
        await page.goto(nav, wait_until="load")
        assert await page.title() == "活動報名｜秀茂坪長者中心（示範）"
        assert "示範用 Demo" in (await page.locator(".demo-ribbon").text_content())
        assert await page.locator('label[for="name_zh"]').is_visible()
        await page.select_option("#activity", "act_opera_1500")
        await page.wait_for_timeout(200)
        opts = await page.locator("#session option").all_text_contents()
        assert any("15:00" in o for o in opts)                       # the 時段 dropdown filled
        await browser.close()


async def test_signup_done_end_to_end(wired):
    h = wired
    aid = await propose_set_url_confirm(h)
    assert await settle(h, lambda: state_of(h, aid) == "done"), f"never finished: {statuses(h, aid)}"
    st = statuses(h, aid)
    assert [s["status"] for s in st] == ["filling", "done"]
    assert st[-1]["detail"]["reference"].startswith("DEMO-")
    shots = st[-1]["screenshots"]
    assert len(shots) == 2 and any("pre_submit" in p for p in shots) and any("post_submit" in p for p in shots)
    for p in shots:
        assert os.path.isfile(p) and os.path.getsize(p) > 1000, p


async def test_hkid_variant_needs_human(wired):
    h = wired
    aid = await propose_set_url_confirm(h, url=f"{h.kernel.settings.public_base_url}/mock-centre/?variant=hkid")
    assert await settle(h, lambda: state_of(h, aid) == "needs_human")
    st = statuses(h, aid)
    assert st[-1]["status"] == "needs_human" and st[-1]["detail"]["reason"] == "forbidden_field"
    assert any("身份證" in x for x in st[-1]["detail"]["forbidden"])


async def test_captcha_variant_needs_human(wired):
    h = wired
    aid = await propose_set_url_confirm(h,
                                        url=f"{h.kernel.settings.public_base_url}/mock-centre/?variant=captcha")
    assert await settle(h, lambda: state_of(h, aid) == "needs_human")
    st = statuses(h, aid)
    assert st[-1]["status"] == "needs_human" and st[-1]["detail"]["reason"] == "captcha"


async def test_login_variant_needs_human(wired):
    h = wired
    aid = await propose_set_url_confirm(h, url=f"{h.kernel.settings.public_base_url}/mock-centre/?variant=login")
    assert await settle(h, lambda: state_of(h, aid) == "needs_human")
    st = statuses(h, aid)
    assert st[-1]["status"] == "needs_human" and st[-1]["detail"]["reason"] == "login"


async def test_payment_variant_needs_human(wired):
    h = wired
    aid = await propose_set_url_confirm(h,
                                        url=f"{h.kernel.settings.public_base_url}/mock-centre/?variant=payment")
    assert await settle(h, lambda: state_of(h, aid) == "needs_human")
    st = statuses(h, aid)
    assert st[-1]["status"] == "needs_human" and st[-1]["detail"]["reason"] == "payment"
