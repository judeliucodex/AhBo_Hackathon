"""QA23 checks 5+6: strangers (INV-7, S1 if broken) and alert buttons.

A stranger gets only 「呢個係私人家庭機械人。」 for messages, commands, m: buttons, alert buttons and voice
notes; /whoami returns only their own id; forged callback data with 阿珍's real outbox id does nothing.
Alert buttons: 我處理緑 acknowledges an escalated fall; 已解決 closes it; 錄段聲俾媽媽 explains; stale call_mum
buttons still behave (mapped to voice_mum).
"""
from __future__ import annotations

import asyncio

import pytest
from qa_helpers import (bot_tasks, confirm, events_of, make_svc_fixture, say, settle, stop_all, tap)
from bot_helpers import CHUN_CHAT, STRANGER, channel

CHAT = "8925008896"
STOCK = "呢個係私人家庭機械人。"


@pytest.fixture
async def svc(tmp_path):
    made: list = []
    s = await make_svc_fixture(tmp_path, made)
    yield s
    await stop_all(made)


def _texts(replies):
    return [r["text"] for r in replies]


# ==================================================================== strangers
STRANGER_TEXTS = ["/start", "/menu", "/today", "/meds", "/cal", "/settings", "/contacts", "/demo", "/health",
                  "/alerts", "/fall", "/sos", "/reset", "/panic", "/mute", "/scenario S4", "/clock 07:00",
                  "/quiet 01:00 02:00", "/checkintime morning 05:00", "/say 婆婆好乖", "/thinking flash max",
                  "/dose s_1200", "/checkin morning", "/knobs", "/plan", "/fact 婆婆中咗六合彩",
                  "/story 以前喺觀塘", "確認", "取消", "你好", "Hello?", "幫我打999"]
STRANGER_BUTTONS = ["m:menu", "m:v:contacts", "m:v:health", "m:v:today", "m:f:contact_add", "m:f:med_add",
                    "m:a:fall", "m:a:reset", "m:a:contact_del:c_chun", "m:a:verify:c_chun", "m:i:_done",
                    "m:ok:deadbeef", "m:no:deadbeef", "m:x", "junk", "b:handled:ob_fake", "b:resolved:ob_fake",
                    "b:voice_mum:ob_fake", "b:confirm:ob_fake"]


async def test_stranger_gets_only_the_stock_reply(svc):
    ch = channel(svc)
    for t in STRANGER_TEXTS:
        r = await say(svc, t, chat=STRANGER)
        assert [x["text"] for x in r] == [STOCK], f"text {t!r}: {r}"
        assert r[0]["buttons"] == [], f"text {t!r} carried buttons"
    for d in STRANGER_BUTTONS:
        r = await tap(svc, d, chat=STRANGER)
        assert r == [], f"button {d!r} produced a message: {r}"   # callback answer only (Delivered)
    # /whoami: their own id only
    r = await say(svc, "/whoami", chat=STRANGER)
    assert r[-1]["text"] == f"你嘅 chat id 係：{STRANGER}", r
    assert CHUN_CHAT not in r[-1]["text"]


async def test_stranger_leaves_no_trace(svc):
    ev_before = svc.db.one("SELECT COUNT(*) n FROM events")["n"]
    vm_before = svc.db.one("SELECT COUNT(*) n FROM voice_messages")["n"]
    for t in STRANGER_TEXTS:
        await say(svc, t, chat=STRANGER)
    for d in STRANGER_BUTTONS:
        await tap(svc, d, chat=STRANGER)
    voice = {"update_id": 991, "message": {"message_id": 991, "date": 0,
                                           "chat": {"id": int(STRANGER), "type": "private"},
                                           "from": {"id": int(STRANGER), "first_name": "x"},
                                           "voice": {"file_id": "fake_file", "duration": 2}}}
    from ahbo.links import telegram
    await telegram.handle_update(svc, voice)
    await settle(svc)
    assert svc.db.one("SELECT COUNT(*) n FROM events")["n"] == ev_before, "stranger traffic recorded events"
    assert svc.db.one("SELECT COUNT(*) n FROM voice_messages")["n"] == vm_before, "stranger voice note stored"
    assert svc.db.kv_get(f"bot_convo:{STRANGER}") is None, "stranger got conversation state"
    assert svc.db.one("SELECT telegram_chat_id FROM contacts WHERE id='c_chun'")["telegram_chat_id"] == CHUN_CHAT


async def test_stranger_cannot_press_ahchuns_real_alert_buttons(svc):
    # escalate a real (operator-triggered) fall and take 阿珍's ALERT outbox row
    oid, inc_id = await _escalate_fall(svc)
    inc_before = svc.db.one("SELECT state FROM incidents WHERE id=?", (inc_id,))["state"]
    ev_before = len(events_of(svc, "family.button"))
    for data in (f"b:handled:{oid}", f"b:resolved:{oid}", f"b:voice_mum:{oid}", f"b:confirm:{oid}",
                 f"m:ok:deadbeef", f"m:a:contact_del:c_chun", f"m:f:contact_edit:c_chun"):
        r = await tap(svc, data, chat=STRANGER)
        assert r == [], f"stranger press {data} produced a message"
    await settle(svc)
    assert len(events_of(svc, "family.button")) == ev_before, "stranger press submitted family.button"
    inc_after = svc.db.one("SELECT state FROM incidents WHERE id=?", (inc_id,))["state"]
    # the flow may advance ESCALATING→REASSURING by itself (F4 finished); a stranger press may never
    # acknowledge or close it
    assert inc_after in ("ESCALATING", "REASSURING"), f"stranger press moved the incident to {inc_after}"
    assert inc_after != inc_before or True  # natural progression allowed; only acknowledge/close is fatal
    # and 阿珍's own pending confirm is unreachable for the stranger
    r = await say(svc, "/mute")                 # 阿珍 now has a pending confirm
    nonce_btn = {b["callback_data"] for r in r for row in r["buttons"] for b in row if b["text"] == "✅ 確認"}
    for data in nonce_btn:
        rr = await tap(svc, data, chat=STRANGER)
        assert rr == []
        await settle(svc)
    assert not svc.db.kv_get("knobs", {}).get("muted"), "stranger confirmed 阿珍's pending change"
    assert svc.db.kv_get(f"bot_convo:{STRANGER}") is None


# ==================================================================== alert buttons (阿珍)
async def _escalate_fall(svc):
    """/fall from Telegram (operator) → wait for escalation → (outbox id of 阿珍's ALERT, incident id)."""
    r = await say(svc, "/fall")
    await confirm(svc, r)
    deadline = asyncio.get_event_loop().time() + 25
    row = None
    while asyncio.get_event_loop().time() < deadline:
        await settle(svc, 0.4)
        rows = [x for x in svc.db.query(
            "SELECT * FROM outbox WHERE kind='ALERT' AND channel='telegram' AND to_contact='c_chun'")]
        if rows:
            row = rows[-1]
            break
    assert row, "no ALERT reached 阿珍 within 25 s of a confirmed /fall"
    import json
    from ahbo.core.jsonutil import loads
    buttons = loads(row["buttons"], [])
    inc_id = row["correlation_id"]
    inc = svc.db.one("SELECT id, state, source, kind FROM incidents WHERE id=?", (inc_id,))
    print(f"FALL: incident {inc_id} state={inc['state']} source={inc['source']} kind={inc['kind']}; "
          f"buttons={[b['text'] for b in buttons]}")
    assert inc["source"] == "operator", "operator /fall incident is not source=operator"
    assert inc["state"] in ("ESCALATING", "REASSURING", "ACKNOWLEDGED"), inc
    assert {b["id"] for b in buttons} == {"handled", "voice_mum", "resolved"}, buttons
    texts = {b["text"] for b in buttons}
    assert texts == {"我處理緊", "錄段聲俾媽媽", "已解決"}, texts
    return row["id"], inc_id


async def test_handled_then_resolved_close_the_incident(svc):
    oid, inc_id = await _escalate_fall(svc)
    r = await tap(svc, f"b:handled:{oid}")
    await settle(svc)
    assert svc.db.one("SELECT state FROM incidents WHERE id=?", (inc_id,))["state"] == "ACKNOWLEDGED"
    fb = events_of(svc, "family.button")[-1]["payload"]
    assert fb["button"] == "handled" and fb.get("outbox_id") == oid and fb.get("via") == "telegram", fb
    r = await tap(svc, f"b:resolved:{oid}")
    await settle(svc)
    assert svc.db.one("SELECT state FROM incidents WHERE id=?", (inc_id,))["state"] == "CLOSED"
    print("handled → ACKNOWLEDGED, resolved → CLOSED ✓")


async def test_voice_mum_and_stale_call_mum_explain_the_voice_path(svc):
    oid, _ = await _escalate_fall(svc)
    tfl_before = len(svc.db.query("SELECT 1 FROM outbox WHERE channel='twilio'"))  # escalation calls exist
    r = await tap(svc, f"b:voice_mum:{oid}")
    assert r and "錄一段語音訊息" in r[-1]["text"], r
    r = await tap(svc, f"b:call_mum:{oid}")     # stale pre-11-Nov data
    assert r and "錄一段語音訊息" in r[-1]["text"], r
    await settle(svc)
    fb = [e["payload"]["button"] for e in events_of(svc, "family.button")]
    assert fb[-2:] == ["voice_mum", "voice_mum"], f"stale call_mum not mapped: {fb}"
    # the mapped presses added NO new Twilio call (the rows above are the escalation's own calls)
    tfl_after = len(svc.db.query("SELECT 1 FROM outbox WHERE channel='twilio'"))
    assert tfl_after == tfl_before, f"voice_mum/call_mum dialled Twilio ({tfl_before} → {tfl_after})"
