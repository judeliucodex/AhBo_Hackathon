"""Shared plain helpers/constants for the links tests (imported as `link_helpers`; no fixtures here — those
live in conftest.py)."""
from __future__ import annotations

import os
from dataclasses import replace
from typing import Any

from fastapi.testclient import TestClient

from ahbo.api.auth import hash_password
from ahbo.core.settings import load_settings

PASSWORD = "correct-horse-battery"
TG_SECRET = "test-telegram-webhook-secret"
TFL_TOKEN = "test-twilio-authtoken"
CHUN_CHAT = "100000001"
BEACON_TOKEN = "beacon-token-123"
BASE_URL = "https://test.invalid"


def make_settings(tmp_path, **kw):
    os.environ.setdefault("AHBO_FAKE", "all")
    s = load_settings()
    base = dict(app_secret="test-app-secret-0123456789abcdef", internal_token="test-internal-token-0123456789",
                session_secret="test-session-secret-xyz", operator_password_hash=hash_password(PASSWORD),
                public_base_url=BASE_URL,
                telegram_webhook_secret=TG_SECRET, twilio_auth_token=TFL_TOKEN,
                twilio_account_sid="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", twilio_from="+85230000000",
                beacon_tokens={"beacon1": BEACON_TOKEN}, family_telegram_chat_ids={"阿珍": CHUN_CHAT})
    base.update(kw)
    return replace(s, data_dir=tmp_path, **base)


def svc_of(c: TestClient) -> Any:
    return c.app.state.svc


def login(c: TestClient) -> None:
    r = c.post("/login", data={"password": PASSWORD, "next": "/operator/"}, follow_redirects=False)
    assert r.status_code == 303, r.text


def allow_chun(svc: Any, chat_id: str = CHUN_CHAT) -> None:
    """Put 阿珍 on the allowlist (the test seed has no chat ids)."""
    svc.db.execute("UPDATE contacts SET telegram_chat_id=? WHERE id='c_chun'", (chat_id,))


def events_of(svc: Any, type_: str) -> list[dict[str, Any]]:
    from ahbo.core.jsonutil import loads
    rows = svc.db.query("SELECT * FROM events WHERE type=? ORDER BY rowid", (type_,))
    for r in rows:
        r["payload"] = loads(r["payload"], {})
    return rows
