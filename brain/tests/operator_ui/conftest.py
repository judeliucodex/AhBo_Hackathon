"""Fixtures for the operator-console Playwright tests. No `ahbo` imports here: these tests run in the
`ahbo-browser` image (which has Playwright + Chromium but not the ahbo package), so this conftest must
stay independent of `tests/conftest.py` (run pytest with --confcutdir pointing at this directory).

In the regular suite (ahbo-api image) the tests skip cleanly: Chromium is not installed there.
"""
from __future__ import annotations

from pathlib import Path

import json

import pytest

REPO = Path(__file__).resolve().parents[3]
SCREENS = REPO / "docs" / "screens" / "operator"


def _chromium_ready() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    with sync_playwright() as p:
        return Path(p.chromium.executable_path).exists()


@pytest.fixture(scope="session")
def browser():
    pytest.importorskip("playwright")
    if not _chromium_ready():
        pytest.skip("Chromium not installed (run these tests in the ahbo-browser image)")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    # the face kit and the console load web fonts from fonts.googleapis.com; abort them so the pages
    # render offline and deterministically (both fall back to the same local font stack)
    ctx.route("**fonts.googleapis.com/**", lambda route: route.abort())
    ctx.route("**fonts.gstatic.com/**", lambda route: route.abort())
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg._ui_errors = errors            # tests assert this stays empty at the end
    yield pg
    ctx.close()


@pytest.fixture
def standin():
    import standin
    with standin.StandIn(REPO / "face") as s:
        yield s


@pytest.fixture
def console(page, standin):
    """Open the console against the stand-in and wait until the stream + mirror are live."""
    page.goto(standin.url + "/operator/")
    page.wait_for_selector("#conn[data-state='open']")
    page.wait_for_selector("#mirror-status:text('live')", timeout=10000)   # snapshot replayed into the face
    return page


def assert_no_page_errors(page) -> None:
    errs = getattr(page, "_ui_errors", [])
    assert not errs, f"console JS errors: {errs}"


def post_body(page, selector: str, url_part: str) -> dict:
    """Click a control and return the JSON body it POSTed (race-free via expect_request)."""
    with page.expect_request(lambda r: r.method == "POST" and url_part in r.url, timeout=8000) as ri:
        page.click(selector)
    return json.loads(ri.value.post_data or "{}")


def frame(page):
    """The face iframe (page_frame for /face/index.html)."""
    for f in page.frames:
        if f.url.endswith("/face/index.html"):
            return f
    raise AssertionError("face iframe not found")


def mirror_face(page) -> str:
    return frame(page).locator("#face").get_attribute("class") or ""


def save_screenshot(page, name: str) -> None:
    SCREENS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENS / name))
