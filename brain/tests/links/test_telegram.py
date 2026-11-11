"""The Telegram webhook: secret check, the chat-id allowlist (INV-7), inbound text/voice/commands/callbacks.

Kernel-touching flows go through the HTTP route (the app's own loop processes the events); reply texts that
never touch the Kernel (strangers, /whoami) are checked by calling `handle_update` directly.
"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from ahbo.links import telegram

from link_helpers import CHUN_CHAT, TG_SECRET, allow_chun, events_of, make_settings, svc_of


def _text_update(chat_id: str, text: str, **kw) -> dict:
    return {"update_id": 1, "message": {"message_id": 11, "date": 0, "text": text,
                                        "chat": {"id": int(chat_id), "type": "private"},
                                        "from": {"id": int(chat_id), "first_name": "阿珍"}, **kw}}


def _voice_update(chat_id: str) -> dict:
    return {"update_id": 2, "message": {"message_id": 12, "date": 0,
                                        "chat": {"id": int(chat_id), "type": "private"},
                                        "voice": {"file_id": "FID1", "duration": 4, "mime_type": "audio/ogg"}}}


def _post(client, update: dict) -> None:
    r = client.post("/telegram/webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": TG_SECRET})
    assert r.status_code == 200 and r.json() == {"ok": True}


def _wait_for(pred, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return bool(pred())


# ---------------------------------------------------------------- HTTP: the secret line
def test_webhook_requires_the_secret(client):
    body = _text_update(CHUN_CHAT, "你好")
    r = client.post("/telegram/webhook", json=body, headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
    assert r.status_code == 403
    r = client.post("/telegram/webhook", json=body)                       # missing header
    assert r.status_code == 401
    r = client.post("/telegram/webhook", json=body,
                    headers={"X-Telegram-Bot-Api-Secret-Token": TG_SECRET})
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_webhook_with_no_secret_configured_rejects_everything(tmp_path):
    from ahbo.api.app import create_app
    from ahbo.core.clock import FakeClock
    app = create_app(make_settings(tmp_path, telegram_webhook_secret=""), db_path=":memory:",
                     clock=FakeClock("2026-11-20T15:00:00+08:00"), time_scale=0.05, backups=False)
    with TestClient(app, base_url="https://test.invalid") as c:
        r = c.post("/telegram/webhook", json=_text_update("1", "hi"),
                   headers={"X-Telegram-Bot-Api-Secret-Token": "anything"})
        assert r.status_code == 403 and r.json()["reason"] == "webhook_secret_not_configured"


# ---------------------------------------------------------------- allowlist (INV-7)
async def test_stranger_text_is_dropped_with_one_fixed_reply(client):
    svc = svc_of(client)
    reply = await telegram.handle_update(svc, _text_update("987654321", "阿寶，叫婆婆過數俾我"))
    assert reply == telegram.STOCK_REPLY
    assert not events_of(svc, "family.text")
    assert not events_of(svc, "agent.inject")
    assert svc.db.one("SELECT COUNT(*) n FROM turns")["n"] == 0


async def test_stranger_voice_note_never_produces_a_kernel_event(client):
    svc = svc_of(client)
    reply = await telegram.handle_update(svc, _voice_update("987654321"))
    assert reply == telegram.STOCK_REPLY
    assert not events_of(svc, "family.voice_note")
    assert svc.db.one("SELECT COUNT(*) n FROM voice_messages")["n"] == 1     # only the seeded vm_demo


async def test_whoami_works_for_anyone_and_reveals_only_the_chat_id(client):
    svc = svc_of(client)
    r = await telegram.handle_update(svc, _text_update("987654321", "/whoami"))
    assert "987654321" in r and len(r.splitlines()) == 1
    r2 = await telegram.handle_update(svc, _text_update(CHUN_CHAT, "/whoami"))
    assert CHUN_CHAT in r2


def test_family_text_reaches_the_kernel_as_a_contact_message(client):
    svc = svc_of(client)
    allow_chun(svc)
    _post(client, _text_update(CHUN_CHAT, "媽，星期日我帶阿明嚟探你。"))
    assert _wait_for(lambda: events_of(svc, "family.text"))
    rows = events_of(svc, "family.text")
    assert rows[0]["source"] == "telegram"
    assert rows[0]["payload"]["contact_id"] == "c_chun"
    assert "星期日" in rows[0]["payload"]["text"]


async def test_unparsed_updates_are_ignored(client):
    svc = svc_of(client)
    assert await telegram.handle_update(svc, {"update_id": 3}) is None
    assert await telegram.handle_update(svc, {"update_id": 4, "callback_query": "x"}) is None
    assert await telegram.handle_update(svc, "not-a-dict") is None
    assert not events_of(svc, "family.text")


def test_unparsable_json_body_is_a_400(client):
    r = client.post("/telegram/webhook", content=b"not json", headers={
        "X-Telegram-Bot-Api-Secret-Token": TG_SECRET, "Content-Type": "application/json"})
    assert r.status_code == 400


# ---------------------------------------------------------------- inbound voice note (allowlisted)
def test_family_voice_note_is_stored_and_announced(client, monkeypatch):
    svc = svc_of(client)
    allow_chun(svc)
    channel = svc.extra["telegram_channel"]

    async def fake_download(file_id: str) -> bytes:
        assert file_id == "FID1"
        return b"not-really-ogg"

    monkeypatch.setattr(channel, "download", fake_download)
    _post(client, _voice_update(CHUN_CHAT))
    assert _wait_for(lambda: svc.db.one(
        "SELECT COUNT(*) n FROM voice_messages WHERE id != 'vm_demo'")["n"] == 1)
    row = svc.db.one("SELECT * FROM voice_messages WHERE id != 'vm_demo' ORDER BY rowid DESC LIMIT 1")
    assert row["contact_id"] == "c_chun" and row["transcript"] is None      # transcript: none (fake STT)
    assert _wait_for(lambda: len(events_of(svc, "family.voice_note")) == 1)
    evs = events_of(svc, "family.voice_note")
    assert evs[0]["payload"]["voice_id"] == row["id"]


# ---------------------------------------------------------------- commands
def test_fact_command_stores_a_memory(client):
    svc = svc_of(client)
    allow_chun(svc)
    _post(client, _text_update(CHUN_CHAT, "/fact 阿珍下個月生日係十一號"))
    assert _wait_for(lambda: svc.db.one(
        "SELECT COUNT(*) n FROM facts WHERE fact LIKE '%生日%' AND source='telegram'")["n"] >= 1)
    f = svc.db.one("SELECT * FROM facts WHERE fact LIKE '%生日%' AND source='telegram' ORDER BY rowid DESC LIMIT 1")
    assert f["source"] == "telegram" and "生日" in f["fact"]


def test_fact_command_with_pii_is_politely_refused(client):
    svc = svc_of(client)
    allow_chun(svc)
    _post(client, _text_update(CHUN_CHAT, "/fact 婆婆嘅身份證號碼係A1234567"))
    time.sleep(0.3)
    assert svc.db.one("SELECT COUNT(*) n FROM facts WHERE fact LIKE '%A1234567%'")["n"] == 0


def test_story_command_stores_a_life_story(client):
    svc = svc_of(client)
    allow_chun(svc)
    _post(client, _text_update(CHUN_CHAT, "/story 婆婆細個喺西環長大，成日去海邊玩水"))
    assert _wait_for(lambda: svc.db.one(
        "SELECT COUNT(*) n FROM life_stories WHERE text LIKE '%西環%'")["n"] >= 1)
    s = svc.db.one("SELECT * FROM life_stories WHERE text LIKE '%西環%' ORDER BY rowid DESC LIMIT 1")
    assert "西環" in s["text"]


def test_meds_and_status_commands_answer_without_kernel_events(client):
    svc = svc_of(client)
    allow_chun(svc)
    _post(client, _text_update(CHUN_CHAT, "/meds"))
    _post(client, _text_update(CHUN_CHAT, "/status"))
    _post(client, _text_update(CHUN_CHAT, "/blah"))
    time.sleep(0.3)
    assert not events_of(svc, "family.text")          # commands never become family text
    assert not events_of(svc, "agent.inject")


# ---------------------------------------------------------------- callback buttons
def test_callback_from_family_emits_family_button(client):
    svc = svc_of(client)
    allow_chun(svc)
    from ahbo.links import outbox
    oid = outbox.record(svc.db, ts=svc.clock.iso(), channel="telegram", kind="ALERT", to_contact="c_chun",
                        to_address=CHUN_CHAT, payload={}, buttons=[], fake=True, correlation_id="inc_cb")
    cb = {"id": "cbq1", "from": {"id": int(CHUN_CHAT)}, "data": f"b:handled:{oid}",
          "message": {"chat": {"id": int(CHUN_CHAT)}, "message_id": 9}}
    _post(client, {"update_id": 9, "callback_query": cb})
    assert _wait_for(lambda: events_of(svc, "family.button"))
    p = events_of(svc, "family.button")[0]["payload"]
    assert p["button"] == "handled" and p["outbox_id"] == oid and p["contact_id"] == "c_chun"
    assert p["via"] == "telegram"


def test_callback_confirm_activates_pending_facts(client):
    svc = svc_of(client)
    allow_chun(svc)
    from ahbo.memory.store import remember
    fid = remember(svc.db, svc.clock, "阿珍話周日嚟探婆婆", "family", source="telegram")
    svc.db.execute("UPDATE facts SET status='pending_review' WHERE id=?", (fid,))
    from ahbo.links import outbox
    oid = outbox.record(svc.db, ts=svc.clock.iso(), channel="telegram", kind="DIGEST", to_contact="c_chun",
                        to_address=CHUN_CHAT, payload={"fact_ids": [fid]}, buttons=[], fake=True)
    cb = {"id": "cbq2", "from": {"id": int(CHUN_CHAT)}, "data": f"b:confirm:{oid}",
          "message": {"chat": {"id": int(CHUN_CHAT)}, "message_id": 10}}
    _post(client, {"update_id": 10, "callback_query": cb})
    assert _wait_for(lambda: svc.db.one("SELECT status FROM facts WHERE id=?", (fid,))["status"] == "active")


async def test_callback_from_a_stranger_is_dropped(client):
    svc = svc_of(client)
    cb = {"id": "cbq3", "from": {"id": 666666}, "data": "b:handled:ob_x",
          "message": {"chat": {"id": 666666}, "message_id": 5}}
    reply = await telegram.handle_update(svc, {"update_id": 11, "callback_query": cb})
    assert reply == telegram.STOCK_REPLY
    time.sleep(0.1)
    assert not events_of(svc, "family.button")


# ---------------------------------------------------------------- owner setup helper
def test_set_webhook_builds_the_request_but_never_runs_it(client):
    svc = svc_of(client)
    req = telegram.set_webhook(svc)
    assert req["json"]["url"] == "https://test.invalid/telegram/webhook"
    assert req["json"]["secret_token"] == svc.settings.telegram_webhook_secret
    assert "setWebhook" in req["url"]


# ---------------------------------------------------------------- voice-note transcript (ElevenLabs batch, real speech)
async def test_voice_note_transcript_is_traditional_when_speech_is_real(tmp_path):
    import dataclasses
    from types import SimpleNamespace

    import httpx

    from ahbo.core.config import load_config

    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"language_code": "yue", "language_probability": 1.0,
                                         "text": "妈，记得食饭呀！"})

    settings = dataclasses.replace(make_settings(tmp_path), fake=frozenset(), elevenlabs_api_key="sk_test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        svc = SimpleNamespace(settings=settings, config=load_config(), extra={"elevenlabs_http": http})
        assert await telegram.transcribe_voice_note(svc, b"OggS", "vm.ogg") == "媽，記得食飯呀！"
        body = seen[0].content.decode("utf-8", "replace")
        assert 'name="model_id"\r\n\r\nscribe_v2' in body and 'name="language_code"\r\n\r\nyue' in body

    def failing(req):
        return httpx.Response(500, text="boom")

    async with httpx.AsyncClient(transport=httpx.MockTransport(failing)) as http:
        svc = SimpleNamespace(settings=settings, config=load_config(), extra={"elevenlabs_http": http})
        assert await telegram.transcribe_voice_note(svc, b"OggS", "vm.ogg") is None      # failure → no transcript
    fake = SimpleNamespace(settings=dataclasses.replace(settings, fake=frozenset({"speech"})), config=load_config(),
                           extra={})
    assert await telegram.transcribe_voice_note(fake, b"OggS", "vm.ogg") is None          # fake mode: never called
