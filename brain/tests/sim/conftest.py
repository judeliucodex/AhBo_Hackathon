"""Fixtures for the browser-iPad tests (Playwright against the sim page served by stub_server.py).

The browser comes from `AHBO_BROWSER_WS` (an ahbo-browser container's `playwright run-server`, the
same endpoint the Action Runner uses) or, failing that, a local Chromium; with neither the tests
skip — so the plain `ahbo-api` full-suite run skips them instead of failing.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent))
from sim_harness import tone_wav  # noqa: E402
from stub_server import StubServer  # noqa: E402

LAUNCH_ARGS = ["--autoplay-policy=no-user-gesture-required", "--mute-audio"]


@pytest.fixture
async def browser():
    ws = os.environ.get("AHBO_BROWSER_WS", "").strip()
    async with async_playwright() as p:
        try:
            b = await (p.chromium.connect(ws, timeout=15000) if ws
                       else p.chromium.launch(args=LAUNCH_ARGS))
        except Exception as e:  # noqa: BLE001 — any launch/connect failure just means "no browser here"
            pytest.skip(f"no Chromium: set AHBO_BROWSER_WS or run in the ahbo-browser image "
                        f"({type(e).__name__}: {e})")
        yield b
        await b.close()


@pytest.fixture
async def page(browser):
    context = await browser.new_context(viewport={"width": 1500, "height": 950})
    pg = await context.new_page()
    yield pg
    await context.close()


@pytest.fixture
async def stub(tmp_path):
    fixed = tmp_path / "fixed"
    fixed.mkdir()
    tone_wav(fixed / "tone.wav", seconds=0.9)
    server = StubServer(media_dir=fixed)
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def sim_page(page, stub):
    """The sim page open against the stub; returns (page, stub). Waits for `online`."""
    await page.goto(f"{stub.url}/sim/?hb_ms=300")
    from sim_harness import wait_online
    await wait_online(page)
    return page, stub
