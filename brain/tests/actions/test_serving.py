"""The mock centre through the REAL app (`ahbo.api.app.create_app`): the platform's static router serves
the page (BUILD_LOG D-3), the component router serves the API, both public/no-auth (§26.4)."""
from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ahbo.api.app import create_app
from ahbo.core.clock import FakeClock
from ahbo.core.settings import load_settings


@pytest.fixture
def client(tmp_path):
    s = replace(load_settings(), data_dir=tmp_path)
    app = create_app(s, db_path=":memory:", clock=FakeClock("2026-11-20T10:00:00+08:00"),
                     time_scale=0.05, backups=False)
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def test_static_page_served_public(client):
    r = client.get("/mock-centre/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    body = r.text
    assert "秀茂坪長者中心（示範）" in body and "示範用 Demo" in body
    for field, fid in (("姓名（中文）", "name_zh"), ("英文姓名", "name_en"), ("年齡組別", "age_group"),
                       ("聯絡電話", "phone"), ("活動", "activity"), ("時段", "session")):
        assert field in body and f'label for="{fid}"' in body
    for asset in ("style.css", "app.js"):
        ra = client.get(f"/mock-centre/{asset}")
        assert ra.status_code == 200
    assert client.get("/mock-centre/does-not-exist.txt").status_code == 404


def test_platform_installs_the_actions_component(client):
    """The real app must pick up `ahbo.actions.wiring` by itself: registered in `svc.installed`, the
    `action.run` dispatcher on the Kernel, the router among the included component routers (AGENT_BRIEF
    convention), and `mock_centre` bound to the app's own DB. (Endpoint reachability is proven by
    `test_register_api_served_public_no_auth`.)"""
    svc = client.app.state.svc
    assert "actions" in svc.installed
    assert "action.run" in svc.kernel.dispatchers
    from ahbo.actions import mock_centre
    assert mock_centre.router in svc.routers
    assert isinstance(mock_centre.registrations(svc.db), list)   # the table exists on the app's DB


def test_register_api_served_public_no_auth(client):
    assert client.get("/healthz").status_code == 200          # sanity: app is up, no login happened
    r = client.get("/mock-centre/api/activities")
    assert r.status_code == 200
    acts = r.json()["activities"]
    assert {a["id"] for a in acts} >= {"act_opera_1500", "act_craft_1430"}
    reg = client.post("/mock-centre/api/register", json={
        "name_zh": "王婆婆", "name_en": "Wong Po-po", "age_group": "75-79", "phone": "91234567",
        "activity": "act_opera_1500", "session": "15:00-16:30"})
    assert reg.status_code == 200
    ref = reg.json()["reference"]
    assert ref.startswith("DEMO-")
    # the registration is in the demo table (not the care tables; Reset demo never clears it)
    svc = client.app.state.svc
    assert svc.db.one("SELECT reference FROM mock_registrations WHERE reference=?", (ref,))
    assert client.post("/mock-centre/api/register", json={"variant": "captcha"}) .status_code == 400
