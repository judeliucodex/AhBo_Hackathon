"""Menus and read-only views: every menu path and typed command answers, and none of them changes anything."""
from __future__ import annotations

import pytest
from bot_helpers import CHUN_CHAT, events_of, labels, say, tap

from ahbo.links.bot.ui import MENU_ITEMS


@pytest.fixture(autouse=True)
def _doctor(monkeypatch):
    from ahbo.ops import doctor

    async def rows(svc):
        return [{"item": "TELEGRAM_BOT_TOKEN", "status": "green", "detail": "set (…ab12)"},
                {"item": "Twilio verified numbers", "status": "red", "detail": "not verified: +85291234567"},
                {"item": "Azure", "status": "fake", "detail": "fake"}]
    monkeypatch.setattr(doctor, "rows", rows)


async def test_start_and_menu_show_the_main_menu(svc):
    for cmd in ("/start", "/menu"):
        [r] = await say(svc, cmd)
        assert r["chat_id"] == CHUN_CHAT
        assert [b for b in labels(r)] == [t for t, _ in MENU_ITEMS]
        assert list(labels(r)) == ["今日狀況", "藥物", "日程", "設定", "聯絡人", "示範控制", "系統健康"]
    [r] = await say(svc, "/start")
    assert "阿珍" in r["text"]


@pytest.mark.parametrize("view,expect", [
    ("today", "今日狀況"), ("meds", "食藥時間表"), ("cal", "日程"), ("set", "靜音時段"), ("contacts", "聯絡人"),
    ("demo", "示範控制"), ("health", "系統健康"), ("alerts", "最近十個通知"), ("knobs", "思考程度"),
    ("dose_pick", "即刻提醒"), ("ck_pick", "問候"),
])
async def test_every_menu_view(svc, view, expect):
    [r] = await tap(svc, f"m:v:{view}")
    assert expect in r["text"], r["text"]
    assert any(d == "m:menu" for d in labels(r).values()) or view in ("dose_pick", "ck_pick")


@pytest.mark.parametrize("cmd,expect", [
    ("/today", "今日狀況"), ("/status", "今日狀況"), ("/meds", "血壓藥"), ("/cal", "覆診"), ("/calendar", "日程"),
    ("/settings", "朝早問候"), ("/contacts", "阿珍"), ("/alerts", "通知"), ("/health", "🔴"), ("/demo", "示範"),
    ("/help", "/fall"), ("/knobs", "Flash"), ("/thinking", "Sonnet"), ("/dose", "時段"), ("/checkin", "問候"),
    ("/scenario", "用法"), ("/nonsense", "唔知呢個指令"),
])
async def test_typed_commands(svc, cmd, expect):
    replies = await say(svc, cmd)
    assert replies and expect in replies[0]["text"], replies
    assert not events_of(svc, "family.text") and not events_of(svc, "settings.changed")


async def test_today_shows_doses_appointments_incident_and_devices(svc):
    [r] = await say(svc, "/today")
    t = r["text"]
    assert "08:00 糖尿藥：已食" in t and "12:00" in t          # seeded dose states
    assert "覆診＠聯合醫院" in t and "事故：而家冇" in t
    assert "iPad：離線" in t and "長者機" in t


async def test_detail_views_for_slot_med_event_contact(svc):
    [r] = await tap(svc, "m:v:slot:s_1200")
    assert "12:00" in r["text"] and "暫停" in "".join(labels(r))
    [r] = await tap(svc, "m:v:med:bp")
    assert "Amlodipine" in r["text"] and "黃／白" in r["text"]
    ev = svc.db.one("SELECT id FROM calendar_events WHERE title='覆診'")
    [r] = await tap(svc, f"m:v:ev:{ev['id']}")
    assert "聯合醫院" in r["text"]
    [r] = await tap(svc, "m:v:contact:c_chun")
    assert "阿珍" in r["text"] and "••••0000" in r["text"] and "+85291230000" not in r["text"]


async def test_health_masks_phone_numbers(svc):
    [r] = await say(svc, "/health")
    assert "+85291234567" not in r["text"] and "••••4567" in r["text"] and "…ab12" in r["text"]


async def test_contacts_never_show_a_full_number(svc):
    [r] = await say(svc, "/contacts")
    assert "+85291230000" not in r["text"] and "91230000" not in r["text"] and "••••0000" in r["text"]
    assert "1. 阿珍" in r["text"]


async def test_alerts_lists_the_last_ten_outbox_rows(svc):
    from ahbo.links import outbox
    for i in range(12):
        outbox.record(svc.db, ts=f"2026-11-20T15:{i:02d}:00+08:00", channel="telegram", kind="INFO",
                      to_contact="c_chun", to_address=CHUN_CHAT, payload={"text": f"通知{i}"}, fake=True)
    [r] = await say(svc, "/alerts")
    assert r["text"].count("• ") == 10 and "通知11" in r["text"] and "通知1\n" not in r["text"]
