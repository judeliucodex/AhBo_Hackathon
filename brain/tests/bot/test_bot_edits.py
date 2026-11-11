"""Edits from Telegram: confirm/cancel, the settings overlay (persisted, read by the Kernel), medicine schedule and
catalogue (R clips re-rendered + reminder_mirror pushed), calendar, contacts, conversation state."""
from __future__ import annotations

from datetime import datetime

from bot_helpers import (
    CHUN_CHAT,
    bot_tasks,
    button,
    commands_of,
    confirm,
    events_of,
    labels,
    say,
    settle,
    tap,
)

from ahbo import settings_store
from ahbo.core.clock import HKT
from ahbo.core.db import open_db
from ahbo.core.jsonutil import loads
from ahbo.kernel import common, scheduler
from ahbo.links.bot import state


# ---------------------------------------------------------------- confirm / cancel
async def test_change_needs_confirm_and_cancel_changes_nothing(svc):
    r = await say(svc, "/quiet 23:00 06:30")
    assert "靜音開始：22:30 → 23:00" in r[-1]["text"] and set(labels(r[-1])) == {"✅ 確認", "❌ 取消"}
    done = await confirm(svc, r, ok=False)
    assert "已取消" in done[-1]["text"]
    assert settings_store.overlay(svc.db) == {} and not events_of(svc, "settings.changed")


async def test_confirm_applies_records_the_event_and_reports(svc):
    r = await say(svc, "/quiet 23:00 06:30")
    nonce_btn = button(r[-1], "✅")
    done = await confirm(svc, r)
    assert "已改設定" in done[-1]["text"] and "23:00" in done[-1]["text"]
    assert settings_store.quiet_hours(svc.db, svc.config) == {"start": "23:00", "end": "06:30"}
    [ev] = events_of(svc, "settings.changed")
    assert ev["source"] == "telegram"
    assert ev["payload"] == {"what": "quiet_hours", "before": {"quiet_start": "22:30", "quiet_end": "07:00"},
                             "after": {"quiet_start": "23:00", "quiet_end": "06:30"}, "by": "c_chun"}
    assert loads(svc.db.one("SELECT quiet_hours FROM resident")["quiet_hours"], {}) == {"start": "23:00",
                                                                                       "end": "06:30"}
    assert commands_of(svc, "device.config_update")[-1]["payload"]["quiet_hours"] == {"start": "23:00",
                                                                                     "end": "06:30"}
    again = await tap(svc, nonce_btn)                     # a second press of the same button does nothing
    assert "過期" in again[-1]["text"] and len(events_of(svc, "settings.changed")) == 1


async def test_typed_confirm_and_pending_text_never_reaches_popo(svc):
    r = await say(svc, "/checkintime morning 09:00")
    assert "確認？" in r[-1]["text"]
    r = await say(svc, "隨便講吓")
    assert "確認" in r[-1]["text"]
    r = await say(svc, "確認")
    assert "朝早問候：09:00" in r[-1]["text"]
    await settle(svc)
    assert not events_of(svc, "family.text")


# ---------------------------------------------------------------- the overlay: persisted and read by the Kernel
async def test_settings_survive_a_restart(make_svc, rerender_calls, tmp_path):
    path = str(tmp_path / "ahbo.db")
    svc = await make_svc(db_path=path)
    r = await say(svc, "/checkintime evening 21:15")
    await confirm(svc, r)
    await svc.stop()
    db = open_db(path)
    try:
        assert settings_store.checkin_times(db, svc.config)["evening"] == "21:15"
    finally:
        db.close()
    svc2 = await make_svc(db_path=path)
    [r] = await say(svc2, "/settings")
    assert "夜晚問候：21:15（已改）" in r["text"]


def _due(svc, prev: str, now: str) -> list:
    d = svc.clock.today()
    at = lambda hm: datetime.combine(d, datetime.strptime(hm, "%H:%M:%S").time(), HKT)
    return [e for e in scheduler.due_events(svc.db, svc.config, at(prev), at(now), set()) if e.type == "checkin.due"]


async def test_moving_the_morning_checkin_moves_checkin_due(svc):
    assert [e.payload["kind"] for e in _due(svc, "08:29:50", "08:30:05")] == ["morning"]
    r = await tap(svc, "m:f:ck_morning")
    assert "而家：08:30" in r[-1]["text"]
    r = await say(svc, "9點")
    await confirm(svc, r)
    assert _due(svc, "08:29:50", "08:30:05") == []
    assert [e.payload["kind"] for e in _due(svc, "08:59:50", "09:00:05")] == ["morning"]


async def test_quiet_hours_change_reaches_the_kernel(svc):
    ctx = svc.kernel.ctx()                              # virtual time 15:00
    assert common.quiet_hours(ctx) is False
    await confirm(svc, await say(svc, "/quiet 14:00 16:00"))
    assert common.quiet_hours(svc.kernel.ctx()) is True
    await svc.kernel.process(__import__("ahbo.core.events", fromlist=["Event"]).Event(
        type="checkin.due", source="scheduler", payload={"kind": "afternoon"}))
    skipped = events_of(svc, "checkin.skipped")
    assert skipped and skipped[-1]["payload"] == {"kind": "afternoon", "reason": "quiet_hours"}


async def test_reset_demo_keeps_the_caregivers_quiet_hours(svc):
    await confirm(svc, await say(svc, "/quiet 21:00 08:00"))
    await confirm(svc, await say(svc, "/reset"))
    assert loads(svc.db.one("SELECT quiet_hours FROM resident")["quiet_hours"], {}) == {"start": "21:00",
                                                                                       "end": "08:00"}


# ---------------------------------------------------------------- medicine schedule
async def test_add_a_dose_slot_rerenders_and_pushes_the_reminder_mirror(svc, rerender_calls):
    r = await tap(svc, "m:f:slot_add")
    assert "第 1/2 步" in r[-1]["text"]
    r = await say(svc, "09:15")
    assert "第 2/2 步" in r[-1]["text"] and "⬜ 血壓藥" in labels(r[-1])
    r = await tap(svc, "m:i:dm")
    r = await tap(svc, "m:i:bp")
    assert "☑️ 血壓藥" in labels(r[-1]) and "已揀：血壓藥、糖尿藥" in r[-1]["text"]
    r = await tap(svc, "m:i:_done")
    assert "加食藥時間：09:15 血壓藥、糖尿藥" in r[-1]["text"]
    done = await confirm(svc, r)
    assert "已加食藥時間" in done[-1]["text"]
    row = svc.db.one("SELECT * FROM med_schedule WHERE time='09:15'")
    assert row["slot_id"] == "s_0915" and loads(row["med_names"]) == ["bp", "dm"] and row["active"] == 1
    await bot_tasks(svc)
    assert rerender_calls == ["render"]
    push = commands_of(svc, "device.config_update")[-1]["payload"]
    assert push["fixed_cache_version"] == "f-test1"
    slots = {s["slot_id"]: s for s in push["reminder_mirror"]["slots"]}
    assert slots["s_0915"]["clip_id"] == "R_s_0915" and [c["label"] for c in slots["s_0915"]["cards"]] == \
        ["血壓藥", "糖尿藥"]
    ev = events_of(svc, "settings.changed")[-1]
    assert ev["payload"]["what"] == "med_schedule" and ev["payload"]["before"] is None
    assert ev["payload"]["after"]["med_names"] == ["bp", "dm"] and ev["source"] == "telegram"
    # the next dose.due follows the new row
    d = svc.clock.today()
    at = lambda hm: datetime.combine(d, datetime.strptime(hm, "%H:%M").time(), HKT)
    due = scheduler.due_events(svc.db, svc.config, at("09:14"), at("09:15"), set())
    assert any(e.type == "dose.due" and e.payload["slot_id"] == "s_0915" for e in due)


async def test_change_toggle_and_delete_a_slot(svc, rerender_calls):
    r = await tap(svc, "m:f:slot_edit:s_0800")
    assert "而家：08:00" in r[-1]["text"]
    r = await say(svc, "08:15")
    assert "而家：糖尿藥" in r[-1]["text"]
    r = await tap(svc, "m:i:_keep")
    assert "之前：08:00 糖尿藥" in r[-1]["text"] and "之後：08:15 糖尿藥" in r[-1]["text"]
    await confirm(svc, r)
    assert svc.db.one("SELECT time FROM med_schedule WHERE slot_id='s_0800'")["time"] == "08:15"
    await confirm(svc, await tap(svc, "m:a:slot_toggle:s_2000"))
    assert svc.db.one("SELECT active FROM med_schedule WHERE slot_id='s_2000'")["active"] == 0
    await confirm(svc, await tap(svc, "m:a:slot_toggle:s_2000"))
    assert svc.db.one("SELECT active FROM med_schedule WHERE slot_id='s_2000'")["active"] == 1
    await confirm(svc, await tap(svc, "m:a:slot_del:s_2000"))
    assert svc.db.one("SELECT * FROM med_schedule WHERE slot_id='s_2000'") is None
    await bot_tasks(svc)
    assert len(rerender_calls) == 4
    mirror = commands_of(svc, "device.config_update")[-1]["payload"]["reminder_mirror"]
    assert [s["slot_id"] for s in mirror["slots"]] == ["s_0800", "s_1200"]


async def test_slot_needs_a_medicine_and_no_clash(svc):
    await tap(svc, "m:f:slot_add")
    r = await say(svc, "12:00")
    r = await tap(svc, "m:i:_done")
    assert "最少揀一隻藥" in r[-1]["text"]
    r = await tap(svc, "m:i:bp")
    r = await tap(svc, "m:i:_done")
    assert "12:00 已經有一個食藥時段" in r[-1]["text"]
    assert svc.db.one("SELECT COUNT(*) n FROM med_schedule")["n"] == 3


# ---------------------------------------------------------------- medicine catalogue
async def test_add_and_edit_a_medicine(svc, rerender_calls):
    await tap(svc, "m:f:med_add")
    r = await say(svc, "Panadol")
    assert "唔好用英文字" in r[-1]["text"] and "第 1/4 步" in r[-1]["text"]
    r = await say(svc, "降压药")                         # Simplified → Traditional (INV-10)
    r = await say(svc, "Losartan")
    r = await tap(svc, "m:i:_skip")
    assert "藥丸顏色" in r[-1]["text"]
    r = await tap(svc, "m:i:bc")
    assert "加藥物：降壓藥（Losartan）" in r[-1]["text"] and "黃／粉紅" in r[-1]["text"]
    await confirm(svc, r)
    m = svc.db.one("SELECT * FROM med_catalogue WHERE yue='降壓藥'")
    assert m and m["en"] == "Losartan" and m["dose"] is None and loads(m["pill"]) == ["--pill-b", "--pill-c"]
    await tap(svc, "m:f:med_edit:bp")
    await tap(svc, "m:i:_keep")
    await tap(svc, "m:i:_keep")
    await say(svc, "10 mg")
    r = await tap(svc, "m:i:_keep")
    assert "之前：血壓藥（Amlodipine）5 mg" in r[-1]["text"] and "之後：血壓藥（Amlodipine）10 mg" in r[-1]["text"]
    await confirm(svc, r)
    assert svc.db.one("SELECT dose FROM med_catalogue WHERE id='bp'")["dose"] == "10 mg"
    await bot_tasks(svc)
    assert len(rerender_calls) == 2
    assert [e["payload"]["what"] for e in events_of(svc, "settings.changed")] == ["med_catalogue"] * 2


async def test_a_medicine_in_use_cannot_be_deleted(svc):
    r = await tap(svc, "m:a:med_del:bp")
    assert "請先喺時間表移除" in r[-1]["text"]
    assert svc.db.one("SELECT id FROM med_catalogue WHERE id='bp'")


# ---------------------------------------------------------------- calendar
async def test_add_change_and_cancel_an_appointment(svc):
    await tap(svc, "m:f:cal_add")
    await tap(svc, "m:i:聽日")
    await say(svc, "10:30")
    await say(svc, "牙醫")
    await say(svc, "觀塘診所")
    r = await tap(svc, "m:i:medical")
    assert "點去" in r[-1]["text"]
    r = await say(svc, "樓下搭11B巴士")
    assert "加約會：2026-11-21 10:30 牙醫＠觀塘診所（醫療）" in r[-1]["text"]
    await confirm(svc, r)
    e = svc.db.one("SELECT * FROM calendar_events WHERE title='牙醫'")
    assert e["datetime"] == "2026-11-21T10:30:00+08:00" and e["kind"] == "medical" and e["source"] == "telegram"
    [v] = await say(svc, "/cal")
    assert "牙醫＠觀塘診所" in v["text"] and "聽日" in v["text"]
    await tap(svc, f"m:f:cal_edit:{e['id']}")
    for _ in range(2):
        await tap(svc, "m:i:_keep")
    await say(svc, "睇牙")
    for _ in range(3):
        r = await tap(svc, "m:i:_keep")
    await confirm(svc, r)
    assert svc.db.one("SELECT title FROM calendar_events WHERE id=?", (e["id"],))["title"] == "睇牙"
    await confirm(svc, await tap(svc, f"m:a:cal_cancel:{e['id']}"))
    assert svc.db.one("SELECT status FROM calendar_events WHERE id=?", (e["id"],))["status"] == "cancelled"
    assert [x["payload"]["what"] for x in events_of(svc, "settings.changed")] == ["calendar"] * 3


async def test_bad_dates_and_english_titles_are_re_asked(svc):
    await tap(svc, "m:f:cal_add")
    r = await say(svc, "2020-01-01")
    assert "過咗" in r[-1]["text"]
    await say(svc, "今日")
    r = await say(svc, "25:00")
    assert "時間唔啱" in r[-1]["text"] or "格式" in r[-1]["text"]
    await say(svc, "下晝3點半")
    r = await say(svc, "Doctor visit")
    assert "唔好用英文字" in r[-1]["text"]
    assert svc.extra and state.load(svc.db, CHUN_CHAT)["form"]["data"]["time"] == "15:30"


# ---------------------------------------------------------------- contacts
async def test_add_a_contact_phone_is_normalised_masked_and_verified_in_fake_mode(svc):
    await tap(svc, "m:f:contact_add")
    await say(svc, "阿明")
    r = await say(svc, "999")
    assert "緊急號碼" in r[-1]["text"] and "第 2/4 步" in r[-1]["text"]
    r = await say(svc, "9876 5432")
    assert "致電次序" in r[-1]["text"]
    await say(svc, "2")
    r = await tap(svc, "m:i:_skip")
    assert "電話 ••••5432" in r[-1]["text"] and "98765432" not in r[-1]["text"]
    done = await confirm(svc, r)
    assert "已加聯絡人：阿明" in done[-1]["text"] and "已核實" in done[-1]["text"]
    c = svc.db.one("SELECT * FROM contacts WHERE name='阿明'")
    assert c["phone"] == "+85298765432" and c["verified"] == 1 and c["priority"] == 2 and c["role"] == "family"
    ev = events_of(svc, "settings.changed")[-1]
    assert ev["payload"]["what"] == "contacts" and ev["payload"]["after"]["phone"] == "••••5432"
    assert "98765432" not in str(ev["payload"])


async def test_you_cannot_lock_yourself_out(svc):
    await tap(svc, "m:f:contact_edit:c_chun")
    await tap(svc, "m:i:_keep")
    await tap(svc, "m:i:_keep")
    await tap(svc, "m:i:_keep")
    r = await say(svc, "123456789")
    assert "唔可以改你自己嘅 Telegram chat id" in r[-1]["text"]
    r = await tap(svc, "m:a:contact_del:c_chun")
    assert "唔可以刪除你自己" in r[-1]["text"]
    assert svc.db.one("SELECT telegram_chat_id FROM contacts WHERE id='c_chun'")["telegram_chat_id"] == CHUN_CHAT


# ---------------------------------------------------------------- conversation state
async def test_form_state_is_kv_backed_and_expires_after_10_minutes(svc):
    await tap(svc, "m:f:slot_add")
    st = svc.db.kv_get(f"bot_convo:{CHUN_CHAT}")
    assert st["form"]["name"] == "slot_add"
    st["ts"] -= 601
    svc.db.kv_set(f"bot_convo:{CHUN_CHAT}", st)
    r = await tap(svc, "m:i:_skip")
    assert "過期" in r[-1]["text"]
    await say(svc, "10:00")                               # no form any more → relayed to 媽媽 as before
    await settle(svc)
    assert events_of(svc, "family.text")[-1]["payload"]["text"] == "10:00"


async def test_text_during_a_form_is_never_relayed_and_cancel_works(svc):
    await tap(svc, "m:f:say")
    r = await say(svc, "取消")
    assert "已取消" in r[-1]["text"]
    await tap(svc, "m:f:quiet")
    await say(svc, "22:00")
    r = await tap(svc, "m:x")
    assert "已取消" in r[-1]["text"]
    await settle(svc)
    assert not events_of(svc, "family.text") and settings_store.overlay(svc.db) == {}
