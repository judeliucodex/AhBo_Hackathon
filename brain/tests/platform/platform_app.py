"""Shared fixtures for the platform/api tests: a real app (in-memory DB, FakeClock, fast timers) behind
Starlette's TestClient, with test secrets."""
from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ahbo.api.app import create_app
from ahbo.api.auth import hash_password
from ahbo.core.clock import FakeClock
from ahbo.core.settings import load_settings

PASSWORD = "correct-horse-battery"
APP_SECRET = "test-app-secret-0123456789abcdef"
INTERNAL = "test-internal-token-0123456789"
_HASH = hash_password(PASSWORD)


def make_settings(tmp_path, **kw):
    os.environ.setdefault("AHBO_FAKE", "all")
    s = load_settings()
    return replace(s, app_secret=APP_SECRET, internal_token=INTERNAL, session_secret="test-session-secret-xyz",
                   operator_password_hash=_HASH, data_dir=tmp_path, public_base_url="https://test.invalid", **kw)


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def client(settings) -> Iterator[TestClient]:
    clock = FakeClock("2026-11-20T10:00:00+08:00")
    app = create_app(settings, db_path=":memory:", clock=clock, time_scale=0.05, backups=False)
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def login(c: TestClient) -> None:
    r = c.post("/login", data={"password": PASSWORD, "next": "/operator/"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    assert "ahbo_session" in c.cookies


def svc_of(c: TestClient):
    return c.app.state.svc


def hello(ws, device_id: str = "ipad-test-1") -> dict:
    ws.send_json({"id": "01HELLO", "type": "hello", "device_id": device_id, "app_version": "0.1.0",
                  "fixed_cache_version": "", "wake_engine": "speech_transcriber"})
    return ws.receive_json()


def drain_until(ws, pred, limit: int = 200) -> list[dict]:
    """Receive frames (acking every command) until pred(frame) is true; returns everything received."""
    got = []
    for _ in range(limit):
        f = ws.receive_json()
        got.append(f)
        if "priority" in f:
            ws.send_json({"id": "a" + f["id"], "type": "ack", "ref": f["id"], "ok": True})
        if pred(f):
            return got
    raise AssertionError(f"condition not met; got {[x.get('type') for x in got]}")
