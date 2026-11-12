"""QA23 check 2: edits with 確認／取消 — med catalogue + dose slots, calendar, quiet hours, check-ins, contacts.

After 確認 the database changed exactly as described; after 取消 nothing changed; half-finished forms expire
after 10 min; typing a normal message mid-form; restart mid-form on the same data dir.
"""
from __future__ import annotations

import time

import pytest
from qa_helpers import (bot_tasks, confirm, events_of, make_svc_fixture, say, settle, stop_all, tap,
                        simplified_hits)

CHAT = "8925008896"


@pytest.fixture
async def svc(tmp_path):
    made: list = []
    s = await make_svc_fixture(tmp_path, made)
    yield s
    await stop_all(made)


def _texts(replies):
    return [r["text"] for r in replies]


def _buttons(replies):
    return {b["text"]: b["callback_data"] for r in replies for row in r["buttons"] for b in row}


def _btn(replies, prefix):
    for t, d in _buttons(replies).items():
        if t.startswith(prefix):
            return d
    raise AssertionError(f"no button {prefix!r} in {_buttons(replies)}")


def _expire(svc, chat=CHAT):
    """Age the conversation state past its 10-minute TTL."""
    st = svc.db.kv_get(f"bot_convo:{chat}")
    assert st, "no conversation state stored"
    st["ts"] = time.time() - 601
    svc.db.kv_set(f"bot_convo:{chat}", st)


# ---------------------------------------------------------------- medicine catalogue
async def test_add_medicine_confirm_changes_db_exactly(svc, tmp_path):
    r = await tap(svc, "m:f:med_add")
    assert "【加藥物】第 1/4 步" in r[-1]["text"]
    r = await say(svc, "胃藥")                       # yue
    assert "第 2/4 步" in r[-1]["text"]
    r = await say(svc, "Antacid")                    # en
    r = await say(svc, "500 mg")                     # dose
    r = await tap(svc, _btn(r, "白／粉紅"))           # pill colours via button
    summary = r[-1]["text"]
    assert "加藥物：胃藥（Antacid）500 mg，藥丸：白／粉紅" in summary, summary
    r = await confirm(svc, r)
    assert any("已加藥物" in t for t in _texts(r)), r
    row = svc.db.one("SELECT * FROM med_catalogue WHERE yue='胃藥'")
    assert row, "medicine not stored"
    from ahbo.core.jsonutil import loads
    assert row["en"] == "Antacid" and row["dose"] == "500 mg"
    assert loads(row["pill"], []) == ["--pill-a", "--pill-c"]
    ev = events_of(svc, "settings.changed")
    assert ev and ev[-1]["payload"]["what"] == "med_catalogue" and ev[-1]["source"] == "telegram"
    assert ev[-1]["payload"]["after"]["yue"] == "胃藥"


async def test_add_medicine_cancel_changes_nothing(svc):
    r = await tap(svc, "m:f:med_add")
    r = await say(svc, "胃藥")
    r = await say(svc, "Antacid")
    r = await say(svc, "500 mg")
    r = await tap(svc, _btn(r, "白／粉紅"))
    n_before = svc.db.one("SELECT COUNT(*) n FROM med_catalogue")["n"]
    r = await confirm(svc, r, ok=False)
    assert any("已取消" in t and "冇改到" in t for t in _texts(r)), r
    assert svc.db.one("SELECT COUNT(*) n FROM med_catalogue")["n"] == n_before, "cancel still wrote a row"
    assert not events_of(svc, "settings.changed"), "cancel recorded a settings.changed event"


async def test_edit_medicine_describes_before_and_after(svc):
    mid = svc.db.one("SELECT id FROM med_catalogue WHERE yue='血壓藥'")["id"]
    r = await tap(svc, f"m:f:med_edit:{mid}")
    assert "而家：血壓藥" in r[-1]["text"], r[-1]["text"]
    r = await tap(svc, "m:i:_keep")                  # keep yue
    r = await say(svc, "-")                          # skip en (typed alias)
    r = await say(svc, "7.5 mg")                     # change dose (seed is 5 mg)
    r = await tap(svc, _btn(r, "白／黃"))
    summary = r[-1]["text"]
    assert "改藥物：" in summary and "之前：" in summary and "之後：" in summary, summary
    assert "7.5 mg" in summary
    r = await confirm(svc, r)
    row = svc.db.one("SELECT * FROM med_catalogue WHERE id=?", (mid,))
    assert row["dose"] == "7.5 mg"
    ev = events_of(svc, "settings.changed")[-1]["payload"]
    assert ev["before"]["dose"] != "7.5 mg" and ev["after"]["dose"] == "7.5 mg"


async def test_delete_unused_medicine(svc):
    r = await tap(svc, "m:f:med_add")
    r = await say(svc, "維他命C")
    r = await say(svc, "-")
    r = await say(svc, "-")
    r = await tap(svc, _btn(r, "白／白"))
    await confirm(svc, r)
    mid = svc.db.one("SELECT id FROM med_catalogue WHERE yue='維他命C'")["id"]
    r = await tap(svc, f"m:a:med_del:{mid}")
    assert "刪除藥物：維他命C" in r[-1]["text"], r[-1]["text"]
    await confirm(svc, r)
    assert svc.db.one("SELECT 1 FROM med_catalogue WHERE id=?", (mid,)) is None


# ---------------------------------------------------------------- dose slots
async def test_add_slot_confirm_then_pause_then_delete(svc):
    r = await tap(svc, "m:f:slot_add")
    assert "【加食藥時間】第 1/2 步" in r[-1]["text"]
    r = await say(svc, "14:30")
    r = await tap(svc, _btn(r, "⬜ 血壓藥"))
    r = await tap(svc, _btn(r, "⬜ 糖尿藥"))
    r = await tap(svc, "m:i:_done")
    summary = r[-1]["text"]
    assert "加食藥時間：14:30 血壓藥、糖尿藥" in summary, summary
    r = await confirm(svc, r)
    assert any("已加食藥時間" in t for t in _texts(r))
    row = svc.db.one("SELECT * FROM med_schedule WHERE time='14:30' AND active=1")
    assert row, "slot not stored"
    from ahbo.core.jsonutil import loads
    assert set(loads(row["med_names"], [])) <= {m["id"] for m in svc.db.query("SELECT id FROM med_catalogue")}

    # pause (one-shot button asks 確認 first)
    r = await tap(svc, f"m:v:slot:{row['slot_id']}")
    r = await tap(svc, _btn(r, "⏸ 暫停"))
    assert "暫停食藥時間" in r[-1]["text"]
    await confirm(svc, r)
    assert svc.db.one("SELECT active FROM med_schedule WHERE slot_id=?", (row["slot_id"],))["active"] == 0

    # resume then delete
    r = await tap(svc, f"m:v:slot:{row['slot_id']}")
    r = await tap(svc, _btn(r, "▶️ 恢復"))
    await confirm(svc, r)
    assert svc.db.one("SELECT active FROM med_schedule WHERE slot_id=?", (row["slot_id"],))["active"] == 1
    r = await tap(svc, f"m:v:slot:{row['slot_id']}")
    r = await tap(svc, _btn(r, "🗑 刪除"))
    assert "刪除食藥時間：14:30" in r[-1]["text"]
    await confirm(svc, r)
    assert svc.db.one("SELECT 1 FROM med_schedule WHERE slot_id=?", (row["slot_id"],)) is None


async def test_slot_clash_and_empty_slot_refused(svc):
    r = await tap(svc, "m:f:slot_add")
    r = await say(svc, "12:00")                       # already exists
    r = await tap(svc, _btn(r, "⬜ 血壓藥"))
    r = await tap(svc, "m:i:_done")
    assert "已經有一個食藥時段" in r[-1]["text"], r[-1]["text"]


# ---------------------------------------------------------------- calendar
async def test_add_event_confirm_exactly_and_cancel_path(svc):
    r = await tap(svc, "m:f:cal_add")
    assert "【加約會】第 1/6 步" in r[-1]["text"]
    r = await tap(svc, _btn(r, "今日"))               # date button
    r = await say(svc, "16:00")
    r = await say(svc, "買餸")
    r = await say(svc, "-")                           # place skipped
    r = await tap(svc, _btn(r, "其他"))               # kind button
    r = await say(svc, "-")                           # route skipped
    summary = r[-1]["text"]
    assert "加約會：" in summary and "買餸" in summary and "16:00" in summary, summary
    r = await confirm(svc, r)
    row = svc.db.one("SELECT * FROM calendar_events WHERE title='買餸'")
    assert row, "event not stored"
    assert row["datetime"][11:16] == "16:00" and row["status"] == "confirmed" and row["source"] == "telegram"

    # cancel the event (one-shot button)
    r = await tap(svc, f"m:v:ev:{row['id']}")
    r = await tap(svc, _btn(r, "🚫 取消約會"))
    assert "取消約會：" in r[-1]["text"] and "買餸" in r[-1]["text"]
    await confirm(svc, r)
    assert svc.db.one("SELECT status FROM calendar_events WHERE id=?", (row["id"],))["status"] == "cancelled"


async def test_event_cancel_button_declined_changes_nothing(svc):
    eid = svc.db.one("SELECT id FROM calendar_events WHERE status != 'cancelled' LIMIT 1")["id"]
    before = svc.db.one("SELECT * FROM calendar_events WHERE id=?", (eid,))
    r = await tap(svc, f"m:v:ev:{eid}")
    r = await tap(svc, _btn(r, "🚫 取消約會"))
    r = await confirm(svc, r, ok=False)
    after = svc.db.one("SELECT * FROM calendar_events WHERE id=?", (eid,))
    assert dict(before) == dict(after), "取消 wrote anyway"
    assert any("已取消，冇改到嘢" in t for t in _texts(r))


# ---------------------------------------------------------------- quiet hours + check-in times
async def test_quiet_hours_form_and_checkin_form(svc):
    r = await tap(svc, "m:f:quiet")
    assert "靜音開始時間" in r[-1]["text"]
    r = await say(svc, "23:00")
    r = await say(svc, "06:30")
    summary = r[-1]["text"]
    assert "靜音開始：22:30 → 23:00" in summary and "靜音結束：07:00 → 06:30" in summary, summary
    r = await confirm(svc, r)
    ov = svc.db.kv_get("settings_overlay")
    assert ov["quiet_start"] == "23:00" and ov["quiet_end"] == "06:30"
    assert svc.db.one("SELECT quiet_hours FROM resident")["quiet_hours"] == '{"start": "23:00", "end": "06:30"}' \
        or "23:00" in svc.db.one("SELECT quiet_hours FROM resident")["quiet_hours"]

    r = await tap(svc, "m:f:ck_morning")
    assert "而家：08:30" in r[-1]["text"]
    r = await say(svc, "9點半")
    assert "朝早問候：08:30 → 09:30" in r[-1]["text"], r[-1]["text"]
    await confirm(svc, r)
    assert svc.db.kv_get("settings_overlay")["morning"] == "09:30"
    # identical quiet start/end refused
    r = await say(svc, "/quiet 08:00 08:00")
    assert "唔可以一樣" in r[-1]["text"], r[-1]["text"]


# ---------------------------------------------------------------- contacts
async def test_add_contact_confirmed_verified_and_masked(svc):
    r = await tap(svc, "m:f:contact_add")
    assert "【加聯絡人】第 1/4 步" in r[-1]["text"]
    r = await say(svc, "家姐")
    r = await say(svc, "9123 4567")
    r = await say(svc, "2")
    r = await say(svc, "-")                           # no chat id
    summary = r[-1]["text"]
    assert "電話 ••••4567" in summary, summary
    assert "999" not in summary.replace("••••", "")   # never a full number
    r = await confirm(svc, r)
    row = svc.db.one("SELECT * FROM contacts WHERE name='家姐'")
    assert row, "contact not stored"
    assert row["phone"] == "+85291234567" and row["verified"] == 1 and row["role"] == "family"
    # shown masked in the view too
    r = await say(svc, "/contacts")
    assert "91234567" not in "\n".join(_texts(r)) and "••••4567" in "\n".join(_texts(r))


async def test_contact_delete_and_self_lockout(svc):
    me = svc.db.one("SELECT id FROM contacts WHERE telegram_chat_id=?", (CHAT,))["id"]
    r = await tap(svc, f"m:a:contact_del:{me}")
    assert "唔可以刪除你自己" in r[-1]["text"], r[-1]["text"]
    # delete another family contact
    r = await tap(svc, "m:f:contact_add")
    r = await say(svc, "阿明")
    r = await say(svc, "-")                           # no phone
    r = await say(svc, "3")
    r = await say(svc, "-")
    await confirm(svc, r)
    cid = svc.db.one("SELECT id FROM contacts WHERE name='阿明'")["id"]
    r = await tap(svc, f"m:a:contact_del:{cid}")
    await confirm(svc, r)
    assert svc.db.one("SELECT 1 FROM contacts WHERE id=?", (cid,)) is None


# ---------------------------------------------------------------- form lifecycle
async def test_half_finished_form_expires_after_10_minutes(svc):
    r = await tap(svc, "m:f:med_add")
    r = await say(svc, "胃藥")
    assert "第 2/4 步" in r[-1]["text"]
    _expire(svc)
    r = await say(svc, "Antacid")
    # expired form: the text must NOT be consumed as form input; it is relayed to 婆婆 instead
    assert not any("第 3/4 步" in t for t in _texts(r)), "expired form still accepted input"
    await settle(svc)
    fam = events_of(svc, "family.text")
    assert fam and fam[-1]["payload"]["text"] == "Antacid", "expired-form text was not relayed to the Kernel"
    # and a form button press says the form expired
    r = await tap(svc, "m:i:_keep")
    assert "過期" in r[-1]["text"], r[-1]["text"]


async def test_normal_message_mid_form_is_consumed_not_relayed(svc):
    r = await tap(svc, "m:f:med_add")
    await say(svc, "胃藥")
    n_fam = len(events_of(svc, "family.text"))
    r = await say(svc, "唔係，我想改時間先")           # plain sentence mid-form
    assert not any("收到" in t for t in _texts(r))
    await settle(svc)
    assert len(events_of(svc, "family.text")) == n_fam, "mid-form text leaked to 婆婆"
    r = await say(svc, "取消")
    assert any("已取消" in t and "冇改到" in t for t in _texts(r)), r


async def test_pending_confirm_typed_words(svc):
    r = await say(svc, "/mute")
    r = await say(svc, "確認")
    assert any("已靜音" in t or "靜音" in t for t in _texts(r)), r
    knobs = svc.db.kv_get("knobs", {})
    assert knobs.get("muted") == 1, knobs
    r = await say(svc, "/unmute")
    r = await say(svc, "取消")
    assert svc.db.kv_get("knobs", {}).get("muted") == 1, "typed 取消 still applied"
    # something else while pending: told to press 確認/取消, not applied
    r = await say(svc, "/unmute")
    r = await say(svc, "今日天氣好好")
    assert any("確認" in t and "取消" in t for t in _texts(r)), r
    assert svc.db.kv_get("knobs", {}).get("muted") == 1


async def test_stale_confirm_nonce_does_not_apply(svc):
    r = await say(svc, "/mute")
    nonce_btn = _btn(r, "✅")
    r = await say(svc, "/unmute")                     # a new pending replaces the old
    new_btn = _btn(r, "✅")
    assert nonce_btn != new_btn
    r = await tap(svc, nonce_btn)                     # stale nonce
    assert any("過期" in t or "做咗" in t for t in _texts(r)), r
    assert not svc.db.kv_get("knobs", {}).get("muted"), "stale nonce applied a change anyway"


async def test_restart_mid_form_on_same_data_dir(tmp_path):
    made: list = []
    db_path = str(tmp_path / "ahbo.sqlite")
    s = await make_svc_fixture(tmp_path, made, db_path=db_path)
    r = await tap(s, "m:f:quiet")
    r = await say(s, "23:30")                         # step 1 answered, form half-done
    assert "靜音結束時間" in r[-1]["text"], r[-1]["text"]
    await stop_all(made)                              # "restart"

    made2: list = []
    s2 = await make_svc_fixture(tmp_path, made2, db_path=db_path)
    try:
        r = await say(s2, "07:15")                    # continue the form on the new server
        assert "靜音開始：22:30 → 23:30" in r[-1]["text"] and "靜音結束：07:00 → 07:15" in r[-1]["text"], f"form lost across restart: {r}"
        r = await confirm(s2, r)
        assert s2.db.kv_get("settings_overlay")["quiet_start"] == "23:30"
        assert s2.db.kv_get("settings_overlay")["quiet_end"] == "07:15"
        # and a pending confirm also survives the restart
        r = await say(s2, "/mute")
        btn = _btn(r, "✅")
        await stop_all(made2)
        made3: list = []
        s3 = await make_svc_fixture(tmp_path, made3, db_path=db_path)
        try:
            r = await tap(s3, btn)
            assert any("已靜音" in t for t in _texts(r)), f"pending confirm lost across restart: {r}"
            assert s3.db.kv_get("knobs", {}).get("muted") == 1
        finally:
            await stop_all(made3)
    finally:
        await stop_all(made2)
