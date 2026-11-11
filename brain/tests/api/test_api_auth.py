"""Auth rules (§26.4, CONTRACTS §8): which routes need the cookie, login rate limit, tokens."""
from __future__ import annotations

import time

from platform_app import APP_SECRET, INTERNAL, PASSWORD, login, svc_of

from ahbo.api import auth


def test_healthz_is_public(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["profile"] == "demo" and "models" in body["fake"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_pages_redirect_to_login(client):
    for path in ("/", "/operator/", "/sim/"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303, path
        assert r.headers["location"].startswith("/login?next=")


def test_api_needs_cookie(client):
    assert client.get("/operator/api/state").status_code == 401
    assert client.get("/operator/stream").status_code == 401
    assert client.post("/operator/api/trigger", json={"kind": "fall"}).status_code == 401
    assert client.post("/session", json={}).status_code == 401
    assert client.get("/media/fixed/manifest.json").status_code == 401


def test_public_static(client):
    assert client.get("/face/").status_code == 200
    assert "text/html" in client.get("/face/index.html").headers["content-type"]
    assert client.get("/face/../PRD_ahbo.md").status_code in (404, 400)
    assert client.get("/face/%2e%2e/PRD_ahbo.md").status_code == 404
    assert client.get("/mock-centre/").status_code in (200, 404)   # folder built by the actions agent


def test_login_flow_and_cookie_flags(client):
    r = client.get("/login")
    assert r.status_code == 200 and "密碼" in r.text
    r = client.post("/login", data={"password": "nope"}, follow_redirects=False)
    assert r.status_code == 401
    r = client.post("/login", data={"password": PASSWORD, "next": "//evil.example/"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/operator/"
    sc = r.headers["set-cookie"].lower()
    assert "ahbo_session=" in sc and "httponly" in sc and "secure" in sc and "samesite=strict" in sc
    assert "max-age=43200" in sc
    assert client.get("/operator/api/state").status_code == 200
    r = client.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    client.cookies.clear()
    assert client.get("/operator/api/state").status_code == 401


def test_login_rate_limit(client):
    codes = [client.post("/login", data={"password": "x"}, headers={"X-Forwarded-For": "203.0.113.9"},
                         follow_redirects=False).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    # even the right password is refused while limited
    r = client.post("/login", data={"password": PASSWORD}, headers={"X-Forwarded-For": "203.0.113.9"},
                    follow_redirects=False)
    assert r.status_code == 429
    # another client is not affected
    r = client.post("/login", data={"password": PASSWORD}, headers={"X-Forwarded-For": "203.0.113.10"},
                    follow_redirects=False)
    assert r.status_code == 303


def test_tampered_cookie_refused(client):
    client.cookies.set("ahbo_session", "forged.value.sig")
    assert client.get("/operator/api/state").status_code == 401


def test_device_token(settings):
    tok = auth.make_device_token(settings, "ipad-air5-1")
    assert tok.startswith("dt.ipad-air5-1.")
    assert auth.check_device_token(settings, tok) == "ipad-air5-1"
    assert auth.check_device_token(settings, tok[:-2] + "xx") is None
    assert auth.check_device_token(settings, tok.replace("ipad-air5-1", "ipad-air5-2")) is None
    old = auth.make_device_token(settings, "d", ttl_s=10, now=time.time() - 100)
    assert auth.check_device_token(settings, old) is None


def test_session_with_device_token_fake_livekit(client):
    tok = auth.make_device_token(svc_of(client).settings, "ipad-1")
    r = client.post("/session", json={"reason": "wake", "mode": "companion"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    body = r.json()
    assert body["text_only"] is True and body["session_id"].startswith("sess_")
    assert svc_of(client).kernel.state.session.id == body["session_id"]


def test_internal_needs_token(client):
    assert client.post("/internal/event", json={"type": "agent.state", "payload": {}}).status_code == 401
    r = client.post("/internal/event", json={"type": "agent.state", "payload": {"state": "idle"}},
                    headers={"Authorization": f"Bearer {INTERNAL}"})
    assert r.status_code == 200
    r = client.post("/internal/event", json={"type": "sensor.fall_suspected", "payload": {}},
                    headers={"Authorization": f"Bearer {INTERNAL}"})
    assert r.status_code == 400
    # the app secret is not the internal token
    assert client.post("/internal/event", json={"type": "agent.state", "payload": {}},
                       headers={"Authorization": f"Bearer {APP_SECRET}"}).status_code == 401


def test_operator_pages_after_login(client):
    login(client)
    assert client.get("/operator/", follow_redirects=False).status_code in (200, 404)
    assert client.get("/", follow_redirects=False).headers["location"] == "/sim/"   # owner decision 11 Nov: no web console; / goes to the browser iPad
