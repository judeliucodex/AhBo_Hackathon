"""QA21 check 3: dose lifecycle (§4.3.2, §24.9, INV-4) — every state × the three questions, guards, snooze, MISSED."""
from __future__ import annotations
import asyncio, sys
sys.path.insert(0, "/repo/brain/tests")
import pytest
from conftest import harness  # noqa: F401
from qa import watch, of, faces, clips, until, ev, req

def row(h, slot="s_1200"):
    return h.kernel.db.one("SELECT * FROM dose_events WHERE slot_id=? AND date=?",
                           (slot, h.kernel.clock.today().isoformat()))

async def due(h, slot="s_1200"):
    h.kernel.clock.set_time(slot[2:4] + ":" + slot[4:])
    await ev(h, "dose.due", "scheduler", slot_id=slot, date=h.kernel.clock.today().isoformat())

def says(h):
    return [x["ui"] for x in h.device.sent if x["type"] == "device.ui" and x["ui"].get("type") == "say_start"]


# ---- questions in every state
async def test_question_before_anything(harness):
    h = harness
    h.kernel.clock.set_time("10:00")                       # s_0800 seeded taken at 08:05
    await h.say("阿寶，我幾時食藥呀？")
    s = says(h)
    assert s and ("十二點" in s[-1]["text"] or "12點" in s[-1]["text"]), s[-1]["text"]
    await h.say("阿寶，我食咗藥未呀？")
    s = says(h)
    assert s and ("八點" in s[-1]["text"] or "朝早" in s[-1]["text"]), s[-1]["text"]


async def test_question_while_reminding(harness):
    h = harness
    await due(h)
    assert row(h)["state"] == "reminding"
    await h.say("我食咗藥未呀？")                            # she asks mid-reminder
    s = says(h)
    assert s and "十二點" in "".join(x["text"] for x in s[-2:]), s[-1]["text"]


# ---- reminder: cards + R clip + inject + reminder session
async def test_reminder_delivery(harness):
    h = harness; watch(h)
    await due(h)
    assert clips(h) == ["R_s_1200"]
    cards = [u["card"]["label"] for u in faces(h, "card")]
    assert cards[0] == "12:00 食藥" and {"血壓藥", "糖尿藥", "膽固醇藥", "薄血丸"} <= set(cards)
    assert await until(lambda: of(h, "agent.inject"))
    inj = of(h, "agent.inject")[0]["payload"]
    assert "提佢食" in inj["instruction"] and "血壓藥" in inj["instruction"]
    assert h.kernel.state.session is not None and h.kernel.state.session.mode == "reminder"


# ---- confirm while REMINDING; double-dose guard; state × question after
async def test_confirm_then_double_guard(harness):
    h = harness
    await due(h)
    await h.say("我食咗喇")
    assert row(h)["state"] == "taken_self_reported" and (row(h)["source"] or "") == "voice"
    r = await req(h, "dose.confirm_request", utterance="我食咗喇")
    assert r == {"ok": False, "reason": "no_dose_pending"}, "second claim must be refused (double-dose guard)"
    await h.say("我食咗藥未呀？")
    s = says(h)
    assert s and "已經食咗" in s[-1]["text"], s[-1]["text"]
    r2 = await h.loop.utterance("我食咗藥未呀？")             # tool result check
    # get_med_status scope=today must show the slot taken
    import json as _j
    tools = h.kernel.db.query("SELECT payload FROM events WHERE type='agent.tool_call' "
                              "AND payload LIKE '%get_med_status%'")
    assert tools, "med questions must be answered via get_med_status (INV-4)"


# ---- confirm outside a pending window must refuse
async def test_confirm_outside_window_refuses(harness):
    h = harness
    h.kernel.clock.set_time("10:00")                        # s_1200 due 12:00 (not within 30 min), s_2000 later
    r = await req(h, "dose.confirm_request", slot_id="s_2000", utterance="食咗")
    assert r == {"ok": False, "reason": "no_dose_pending"}
    r = await req(h, "dose.confirm_request", slot_id="s_0800", utterance="食咗")
    assert r == {"ok": False, "reason": "no_dose_pending"}, "already-taken dose must not confirm again"
    assert row(h, "s_2000") is None and row(h, "s_0800")["state"] == "taken_self_reported"


async def test_voice_confirm_for_scheduled_slot_refuses(harness):
    h = harness
    h.kernel.clock.set_time("19:45")                        # s_2000 within 30 min of due but still scheduled
    r = await req(h, "dose.confirm_request", slot_id="s_2000", utterance="食咗")
    assert r == {"ok": False, "reason": "no_dose_pending"}, "voice confirm while SCHEDULED must refuse"
    assert row(h, "s_2000") is None


# ---- snooze limits and return
async def test_snooze_limits(harness):
    h = harness; watch(h)
    await due(h)
    r = await req(h, "dose.snooze_request", minutes=30)
    assert r["ok"] and r["minutes"] == 15, "snooze clamped to 15 min"
    assert row(h)["state"] == "snoozed"
    r = await req(h, "dose.snooze_request", minutes=10)
    assert r["ok"] and row(h)["snoozes"] == 2
    assert await req(h, "dose.snooze_request", minutes=10) == {"ok": False, "reason": "max_snoozes"}
    h.kernel.clock.advance(16 * 60)
    await ev(h, "schedule.tick", "scheduler")
    assert await until(lambda: (row(h) or {}).get("state") == "reminding")
    assert await until(lambda: len(clips(h)) == 2)          # re-reminder after snooze expiry


# ---- re-prompt at T+15, T+30 and MISSED at T+60 → INFO to 阿珍
async def test_reprompts_and_missed(harness):
    h = harness; watch(h)
    await due(h)
    h.kernel.clock.advance(15 * 60)
    await ev(h, "schedule.tick", "scheduler")
    assert await until(lambda: len(clips(h)) == 2)
    h.kernel.clock.advance(15 * 60)
    await ev(h, "schedule.tick", "scheduler")
    assert await until(lambda: len(clips(h)) == 3)
    h.kernel.clock.advance(31 * 60)                         # T+61
    await ev(h, "schedule.tick", "scheduler")
    assert row(h)["state"] == "missed"
    fam = h.kernel.db.query("SELECT payload FROM commands WHERE type='family.send'")
    assert fam and '"INFO"' in fam[-1]["payload"] and "s_1200" in fam[-1]["payload"]
    assert await until(lambda: len(clips(h)) == 3)          # no further reminders after missed


# ---- confirm while MISSED within 3 h still accepted; after 3 h refused
async def test_confirm_missed_window(harness):
    h = harness
    await due(h)
    h.kernel.clock.advance(61 * 60)
    await ev(h, "schedule.tick", "scheduler")
    assert row(h)["state"] == "missed"
    r = await req(h, "dose.confirm_request", utterance="頭先食咗")
    assert r["ok"] and row(h)["state"] == "taken_self_reported"
    # fresh slot: beyond 3 h
    h.kernel.clock.set_time("20:00")
    await ev(h, "dose.due", "scheduler", slot_id="s_2000", date=h.kernel.clock.today().isoformat())
    h.kernel.clock.advance(201 * 60)
    await ev(h, "schedule.tick", "scheduler")
    assert row(h, "s_2000")["state"] == "missed"
    r = await req(h, "dose.confirm_request", slot_id="s_2000", utterance="食咗")
    assert r == {"ok": False, "reason": "no_dose_pending"}, "MISSED beyond 3 h must refuse"


# ---- reminders fire at the right virtual times through the real scheduler crossing
async def test_scheduler_crossing_fires_dose(harness):
    from ahbo.kernel.scheduler import Scheduler
    h = harness; watch(h)
    sched = Scheduler(h.kernel)
    h.kernel.clock.set_time("11:59")
    evs = await sched.tick()
    assert not [e for e in evs if e.type == "dose.due"]
    h.kernel.clock.advance(61)                              # natural move, crosses 12:00 (≤120 s rule)
    evs = await sched.tick()
    dues = [e for e in evs if e.type == "dose.due"]
    assert [e.payload["slot_id"] for e in dues] == ["s_1200"]
    assert await until(lambda: "R_s_1200" in clips(h))
    assert row(h)["state"] == "reminding"


# ---- offline touch confirmation (iPad mirror, source touch_offline)
async def test_offline_touch_confirm(harness):
    h = harness
    await due(h)                                            # s_1200 → REMINDING
    await ev(h, "device.face_event", "device", event={"type": "reminder_confirmed", "slot_id": "s_1200",
                                                      "at": "2026-11-20T12:01:00+08:00", "source": "touch_offline"})
    r = row(h, "s_1200")
    assert r["state"] == "taken_self_reported" and r["source"] == "touch_offline"
    h.kernel.clock.set_time("14:00")                        # far outside any window
    await ev(h, "device.face_event", "device", event={"type": "reminder_confirmed", "slot_id": "s_2000",
                                                      "at": "2026-11-20T14:00:00+08:00", "source": "touch_offline"})
    assert row(h, "s_2000") is None, "offline confirm far outside the window must be ignored"
