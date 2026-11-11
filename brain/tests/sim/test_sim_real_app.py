"""Real-app smoke test for the browser iPad (run only when AHBO_SMOKE_URL is set).

It logs into the real `ahbo-api` with the throwaway smoke password, opens /sim/ (the sim connects to
the real Device Hub with the operator cookie, `?sim=1`), screenshots the frame, then drives the
**real** S2 and S4 scenarios through `POST /operator/api/scenario/{name}` with the same cookie and
screenshots what lands in the sim's face. Fake mode throughout: no keys, no calls, no messages.
Screenshots go to docs/screens/sim/. Skip everywhere else (the normal full suite never sets it).
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.browser

SMOKE_URL = os.environ.get("AHBO_SMOKE_URL", "").strip()
SMOKE_PW = os.environ.get("AHBO_SMOKE_PW", "")
OUT = Path(os.environ.get("AHBO_SMOKE_OUT", "/repo/docs/screens/sim"))


async def _wait(page, cond, msg: str, timeout_s: float) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        out = cond()
        if asyncio.iscoroutine(out):
            out = await out
        if out:
            return
        if loop.time() > deadline:
            raise AssertionError(msg)
        await asyncio.sleep(0.15)


def face_frame(page):
    for f in page.frames:
        if "/face/" in f.url:
            return f
    raise AssertionError("face iframe not loaded")


async def test_real_app_smoke(browser):
    if not SMOKE_URL:
        pytest.skip("real-app smoke run only: set AHBO_SMOKE_URL (and AHBO_SMOKE_PW)")

    context = await browser.new_context(viewport={"width": 1500, "height": 950})
    page = await context.new_page()
    try:
        # ---- login (the real /login flow; the cookie then carries /sim/ and the WS)
        await page.goto(f"{SMOKE_URL}/login", wait_until="domcontentloaded")
        await page.fill("#pw", SMOKE_PW)
        await page.click("button[type=submit]")
        await page.wait_for_url(f"{SMOKE_URL}/operator/**", timeout=10000)

        # ---- open the browser iPad; it must come online against the real hub
        await page.goto(f"{SMOKE_URL}/sim/")
        await page.locator("#conn[data-state='online']").wait_for(timeout=15000)
        face = face_frame(page)
        await face.locator("#face").wait_for(state="attached", timeout=8000)
        OUT.mkdir(parents=True, exist_ok=True)
        cookies = {c["name"]: c["value"] for c in await context.cookies()}
        import httpx
        async with httpx.AsyncClient(base_url=SMOKE_URL, cookies=cookies, timeout=15) as client:
            r = await client.post("/operator/api/trigger", json={"kind": "reset"})
        assert r.status_code == 200 and r.json().get("ok"), f"reset refused: {r.text[:200]}"
        await page.wait_for_timeout(2000)                        # replay + reset settle
        await page.locator("#bezel").screenshot(path=str(OUT / "00_idle.png"))

        # ---- S2: 12:00 dose reminder reply (⏰ card, four pill chips, ✓ step)
        async with httpx.AsyncClient(base_url=SMOKE_URL, cookies=cookies, timeout=15) as client:
            r = await client.post("/operator/api/scenario/S2")
        assert r.status_code == 200 and r.json().get("ok"), f"scenario S2 refused: {r.text[:200]}"

        await _wait(page, lambda: face.locator("[data-id='next_dose']").count(),
                    "S2: no next_dose card in the sim frame", 40)
        await _wait(page, lambda: face.locator(".step.done").count(),
                    "S2: no ✓ step in the sim frame", 40)
        await _wait(page, lambda: face.locator("[data-id^='med_']").count(),
                    "S2: fewer than four pill chips in the sim frame", 40)
        await _wait(page, lambda: page.locator("#sayText").is_enabled(),
                    "S2: text box not enabled by the real session.open", 20)
        n_med = await face.locator("[data-id^='med_']").count()
        assert n_med >= 4, f"S2: only {n_med} pill chips"
        await page.wait_for_timeout(2500)                        # cards light up as the reply is spoken
        await page.locator("#bezel").screenshot(path=str(OUT / "S2_01_reply_cards.png"))

        # ---- S4: fall → alarm, 跌倒處理 steps, 我冇事 + countdown, then escalation cards
        async with httpx.AsyncClient(base_url=SMOKE_URL, cookies=cookies, timeout=15) as client:
            r = await client.post("/operator/api/scenario/S4")
        assert r.status_code == 200 and r.json().get("ok"), f"scenario S4 refused: {r.text[:200]}"

        await _wait(page, lambda: face.locator("#face.alarm").count(),
                    "S4: face never went alarm", 30)
        await _wait(page, lambda: face.locator(".stepgroup[data-id='fall']").count(),
                    "S4: no 跌倒處理 step group in the sim frame", 30)
        await _wait(page, lambda: face.locator("#okbtn.on").count(),
                    "S4: 我冇事 button never shown", 60)
        await _wait(page, lambda: face.locator(".chip.count").count(),
                    "S4: no countdown chip", 30)
        await page.locator("#bezel").screenshot(path=str(OUT / "S4_01_alarm_countdown.png"))

        async def esc_cards_on() -> bool:
            tell = await face.locator("[data-id='esc_tell'].on").count()
            call = await face.locator("[data-id='esc_call'].on").count()
            return tell > 0 and call > 0

        await _wait(page, esc_cards_on,
                    "S4: escalation cards (esc_call/esc_tell) never lit up", 90)
        await _wait(page, lambda: face.locator(
            ".stepgroup[data-id='fall'] .step[data-id='ask'].failed").count(),
            "S4: ask step never failed (冇回應)", 30)
        await page.wait_for_timeout(1500)
        await page.locator("#bezel").screenshot(path=str(OUT / "S4_02_escalation.png"))
    finally:
        await context.close()
