"""Real-app smoke test (GLM task 03): log in to the real `ahbo-api` (fake mode), open /operator/, click
**S2**, and assert the live mirror shows the S2 cards (⏰ 12:00 食藥 + the four medicine chips).
Screenshots land in docs/screens/operator/.

Needs a running app; the docker orchestration (private network, throwaway secrets — never the real .env)
is in `smoke_real_app.sh` next to this file. Skipped when AHBO_SMOKE_BASE_URL / AHBO_SMOKE_PASSWORD are
unset, so the regular suite (ahbo-api image) skips it cleanly.
"""
from __future__ import annotations

import os

import pytest

from conftest import SCREENS, assert_no_page_errors, frame

MEDS = ("med_bp", "med_dm", "med_chol", "med_asp")     # seed/demo.yaml catalogue ids


def test_smoke_s2_shows_med_cards_in_mirror(page):
    base = os.environ.get("AHBO_SMOKE_BASE_URL", "")
    password = os.environ.get("AHBO_SMOKE_PASSWORD", "")
    if not base or not password:
        pytest.skip("real-app smoke needs AHBO_SMOKE_BASE_URL + AHBO_SMOKE_PASSWORD (run smoke_real_app.sh)")
    page.set_default_timeout(30000)

    # log in (operator cookie)
    page.goto(base + "/login")
    page.fill("#pw", password)
    page.click("button[type=submit]")
    page.wait_for_url("**/operator/")
    page.wait_for_selector("#conn[data-state='open']", timeout=20000)
    page.wait_for_selector("#mirror-status:text('live')", timeout=20000)

    # S2: reset → clock anchor → 「阿寶，我幾時食藥呀？」 → get_med_status → cards
    page.click("button[data-scenario='S2']")
    fl = frame(page)
    fl.wait_for_selector("#cards [data-id='next_dose']", timeout=60000)
    assert "12:00 食藥" in fl.locator("#cards [data-id='next_dose']").inner_text()
    for med in MEDS:
        fl.wait_for_selector(f"#cards [data-id='{med}']", timeout=15000)
    assert "血壓藥" in fl.locator("#cards [data-id='med_bp']").inner_text()
    assert "薄血丸" in fl.locator("#cards [data-id='med_asp']").inner_text()

    # transcript shows her question and Ah Bo's reply
    page.wait_for_selector("#transcript-body .tline.user", timeout=30000)
    assert "我幾時食藥" in page.locator("#transcript-body").inner_text()

    # screenshots: the whole console, then the mirror alone
    SCREENS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENS / "smoke_S2_console.png"))
    page.locator("#mirror-wrap").screenshot(path=str(SCREENS / "smoke_S2_mirror.png"))
    assert_no_page_errors(page)


def test_smoke_s4_outbox_and_panic(page):
    """S4 end to end against the REAL links code: incident alarm in the mirror, a fake Telegram ALERT and a
    fake Twilio call (with TwiML) appearing live in the Outbox panel, both buttons pressable via the real
    press wiring, then Esc = Panic resets the face."""
    base = os.environ.get("AHBO_SMOKE_BASE_URL", "")
    password = os.environ.get("AHBO_SMOKE_PASSWORD", "")
    if not base or not password:
        pytest.skip("real-app smoke needs AHBO_SMOKE_BASE_URL + AHBO_SMOKE_PASSWORD (run smoke_real_app.sh)")
    page.set_default_timeout(30000)

    page.goto(base + "/login")
    page.fill("#pw", password)
    page.click("button[type=submit]")
    page.wait_for_url("**/operator/")
    page.wait_for_selector("#conn[data-state='open']", timeout=20000)
    page.wait_for_selector("#mirror-status:text('live')", timeout=20000)

    # S4: the beacon falls → alarm mood + ok_button + countdown in the mirror (what Po-po sees)
    page.click("button[data-scenario='S4']")
    fl = frame(page)
    fl.wait_for_function("() => document.getElementById('face').classList.contains('alarm')", timeout=60000)
    page.wait_for_selector("#top-badges .badge.alarm", timeout=15000)      # next state snapshot (≤0.5 s)
    assert "INCIDENT" in page.locator("#top-badges").inner_text()
    page.wait_for_selector("#state-body .incident-box", timeout=30000)

    # the fake Telegram ALERT lands in the Outbox panel live, with its pressable buttons
    page.wait_for_selector("#outbox-body .obx", timeout=60000)
    page.wait_for_selector("#outbox-body button:has-text('我處理緊')", timeout=60000)
    # the fake Twilio call row arrives with its TwiML and a press-1 button
    page.wait_for_selector("#outbox-body pre:has-text('<Response>')", timeout=60000)
    twilio_btn = page.locator("#outbox-body button", has_text="press 1")
    twilio_btn.wait_for(timeout=60000)

    # press both buttons through the REAL press wiring (family.button / phone.call_status acknowledged)
    page.locator("#outbox-body button", has_text="我處理緊").click()
    page.wait_for_selector("#actlog-body .act:has-text('press handled')", timeout=20000)
    twilio_btn.click()
    page.wait_for_selector("#actlog-body .act:has-text('press press1')", timeout=20000)
    # the SSE outbox update repaints the call row as acknowledged
    page.wait_for_function(
        "() => document.getElementById('outbox-body').textContent.includes('acknowledged')", timeout=30000)

    # Esc = Panic against the real backend: stop_all, face reset (alarm mood gone)
    page.keyboard.press("Escape")
    page.wait_for_selector(".toast.panic", timeout=15000)
    fl.wait_for_function("() => !document.getElementById('face').classList.contains('alarm')", timeout=20000)
    assert "PANIC" in page.locator("#actlog-body").inner_text()

    SCREENS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENS / "smoke_S4_console.png"))
    page.locator("#p-outbox").screenshot(path=str(SCREENS / "smoke_S4_outbox.png"))
    assert_no_page_errors(page)
