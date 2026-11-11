"""The fakes (§26.3) and the console helpers: the fake call status sequence, `press` paths, the INFO rate-limit
merge, and the fake inbound voice note / text."""
from __future__ import annotations

import asyncio

from ahbo.core.events import Event, cmd
from ahbo.links import outbox
from ahbo.links.wiring import inject_text, inject_voice_note, press

from link_helpers import events_of, svc_of


async def fall_to_escalating(s):
    await s.kernel.process(Event(type="sensor.fall_suspected", source="beacon",
                                 payload={"peak_g": 3.1, "beacon_id": "beacon1", "battery": 0.8}))
    deadline = asyncio.get_running_loop().time() + 30
    while asyncio.get_running_loop().time() < deadline:
        st = s.incident_state()
        if st == "ESCALATING":
            return True
        await asyncio.sleep(0.05)
    return s.incident_state() == "ESCALATING"


def twilio_rows(s):
    return [r for r in s.outbox() if r["channel"] == "twilio"]


def telegram_rows(s):
    import json
    rows = []
    for r in s.outbox():
        if r["channel"] == "telegram":
            if isinstance(r.get("buttons"), str):          # EvalSystem.outbox() only parses `payload`
                r["buttons"] = json.loads(r["buttons"] or "[]")
            rows.append(r)
    return rows


# ---------------------------------------------------------------- fake Twilio: outbox TwiML + status sequence
async def test_fake_call_sequence_and_twiml(system):
    s = system
    assert await fall_to_escalating(s)
    await s.wait(8)                       # the sequence runs over 6 s × scale
    rows = twilio_rows(s)
    assert len(rows) == 1
    r = rows[0]
    assert r["fake"] and r["to_contact"] == "c_chun"      # owner rule 11 Nov: the caregiver is called
    twiml = r["payload"]["twiml"]
    assert '<Play loop="2">' in twiml and "<Gather" in twiml and "/twilio/gather" in twiml
    statuses = [e["payload"]["status"] for e in s.events("phone.call_status")]
    assert statuses[:3] == ["ringing", "in-progress", "completed"]
    assert all(e["payload"].get("incident_id") == r["correlation_id"] for e in s.events("phone.call_status"))


async def test_press1_acknowledges_the_call(system):
    s = system
    assert await fall_to_escalating(s)
    await s.wait(1)
    r = twilio_rows(s)[0]
    res = await press(s, r["id"], "press1")
    assert res["ok"] and res["via"] == "operator_fake"
    await s.wait(1)
    assert s.incident_state() == "ACKNOWLEDGED"
    acks = [e for e in s.events("phone.call_status") if e["payload"]["status"] == "acknowledged"]
    assert acks and acks[-1]["payload"]["outbox_id"] == r["id"]
    assert acks[-1]["payload"]["call_id"] == r["payload"]["call_id"]
    assert outbox.get(s.db, r["id"])["payload"].get("via") == "operator_fake"   # honest in the outbox too (INV-8)


async def test_press_bad_targets(system):
    s = system
    await s.kernel.emit(cmd("family.send", 1, kind="INFO", contact_ids=["c_chun"], text="試試"))
    await s.wait(1)
    t = telegram_rows(s)[0]
    assert (await press(s, "ob_missing", "handled"))["reason"] == "not_found"
    assert (await press(s, t["id"], "press1"))["reason"] == "bad_button"     # press1 is for calls only


# ---------------------------------------------------------------- press on a Telegram ALERT row
async def test_press_handled_then_resolved(system):
    s = system
    assert await fall_to_escalating(s)
    await s.wait(1)
    alert = [r for r in telegram_rows(s) if r["kind"] == "ALERT"][0]
    assert alert["buttons"], "the ALERT row carries buttons"
    texts = {b["id"]: b["text"] for b in alert["buttons"]}
    assert texts["handled"] == "我處理緊" and texts["resolved"] == "已解決"
    res = await press(s, alert["id"], "handled")
    assert res["ok"]
    await s.wait(1)
    assert s.incident_state() == "ACKNOWLEDGED"
    btns = [e for e in s.events("family.button") if e["payload"].get("outbox_id") == alert["id"]]
    assert btns[-1]["payload"]["via"] == "operator_fake" and btns[-1]["source"] == "telegram"
    # the NOTIFY confirmation the Kernel sends after an acknowledgement
    await s.wait(2)
    assert any(r["kind"] == "NOTIFY" for r in telegram_rows(s))
    res = await press(s, alert["id"], "resolved")
    await s.wait(1)
    assert s.incident_state() == "CLOSED"


async def test_press_call_mum_is_recorded_but_never_dials(system):
    s = system
    assert await fall_to_escalating(s)
    await s.wait(1)
    alert = [r for r in telegram_rows(s) if r["kind"] == "ALERT"][0]
    before = len(twilio_rows(s))
    res = await press(s, alert["id"], "call_mum")
    assert res["ok"] and "冇撥號" in res.get("note", "")
    await s.wait(1)
    assert len(twilio_rows(s)) == before            # no call was placed by the button


async def test_press_confirm_activates_the_rows_facts(system):
    s = system
    from ahbo.memory.store import remember
    fid = remember(s.db, s.clock, "阿珍話周日三點嚟", "family", source="telegram")
    s.db.execute("UPDATE facts SET status='pending_review' WHERE id=?", (fid,))
    oid = outbox.record(s.db, ts=s.clock.iso(), channel="telegram", kind="DIGEST", to_contact="c_chun",
                        to_address=None, payload={"fact_ids": [fid]}, fake=True)
    res = await press(s, oid, "confirm")
    assert res["ok"] and res["confirmed"] == [fid]
    assert s.db.one("SELECT status FROM facts WHERE id=?", (fid,))["status"] == "active"


# ---------------------------------------------------------------- INFO rate limit: ≤ 1 per 10 min, merged
async def test_info_rate_limit_merges_but_alert_is_exempt(system):
    s = system
    link = s.svc.extra["family_link"]
    link.info_window_s = 20.0                     # 1 real second at the test scale; flush is called directly
    await s.kernel.emit(cmd("family.send", 1, kind="INFO", contact_ids=["c_chun"], text="第一則通知"))
    await s.kernel.emit(cmd("family.send", 1, kind="INFO", contact_ids=["c_chun"], text="第二則通知"))
    await s.kernel.emit(cmd("family.send", 1, kind="INFO", contact_ids=["c_chun"], text="第三則通知"))
    await s.wait(2)
    rows = [r for r in telegram_rows(s) if r["kind"] == "INFO"]
    assert [r["status"] for r in rows] == ["sent", "held", "held"], [r["status"] for r in rows]
    assert "第二則通知" not in rows[0]["payload"]["text"] and "第三則通知" not in rows[0]["payload"]["text"]
    merged_id = await link.flush("c_chun")        # the window ends → one merged message
    assert merged_id
    held = [rows[1], rows[2]]
    for h in held:
        assert outbox.get(s.db, h["id"])["status"] == "merged"
    merged = outbox.get(s.db, merged_id)
    assert "2 則通知合併" in merged["payload"]["text"]
    assert "第二則通知" in merged["payload"]["text"] and "第三則通知" in merged["payload"]["text"]
    assert merged["payload"]["merged_from"] == [h["id"] for h in held]
    # ALERT is exempt from the rate limit
    await s.kernel.emit(cmd("family.send", 0, kind="ALERT", contact_ids=["c_chun"], text="緊急",
                            incident_id="inc_rl"))
    await s.wait(1)
    alerts = [r for r in telegram_rows(s) if r["kind"] == "ALERT"]
    assert len(alerts) == 1 and alerts[0]["status"] == "sent"


async def test_alert_row_carries_buttons_and_incident_link(system):
    s = system
    await s.kernel.emit(cmd("family.send", 0, "inc_bt", kind="ALERT", contact_ids=["c_chun"],
                            text="王婆婆喺屋企懷疑跌倒", incident_id="inc_bt", photo_id=None))
    await s.wait(1)
    r = [x for x in telegram_rows(s) if x["kind"] == "ALERT"][0]
    assert r["correlation_id"] == "inc_bt"
    assert {b["id"] for b in r["buttons"]} == {"handled", "resolved"}       # call_mum is dropped in v1
    assert all(b["callback_data"].startswith("b:") for b in r["buttons"])


# ---------------------------------------------------------------- console injects (§26.3)
async def test_inject_text_and_voice_note(system):
    s = system
    r1 = await inject_text(s, "媽，記得食飯呀！")
    assert r1["ok"] and r1["contact_id"] == "c_chun" and r1["via"] == "operator_fake"
    await s.wait(2)
    texts = [e for e in s.events("family.text") if e["payload"].get("via") == "operator_fake"]
    assert texts and "記得食飯" in texts[-1]["payload"]["text"]
    r2 = await inject_voice_note(s)
    assert r2["ok"] and r2["voice_id"]
    await s.wait(2)
    vm = s.db.one("SELECT * FROM voice_messages WHERE id=?", (r2["voice_id"],))
    assert vm["contact_id"] == "c_chun" and vm["duration_s"] and vm["duration_s"] > 0
    notes = [e for e in s.events("family.voice_note") if e["payload"].get("via") == "operator_fake"]
    assert notes[-1]["payload"]["voice_id"] == r2["voice_id"]


async def test_inject_rejects_empty_text(system):
    s = system
    assert (await inject_text(s, "   "))["reason"] == "bad_args"
