"""Dose lifecycle (§4.3.2, §24.9, INV-4)."""
from __future__ import annotations

from kt import clips, ev, faces, of, req, until


def row(h, slot="s_1200"):
    return h.kernel.db.one("SELECT * FROM dose_events WHERE slot_id=? AND date=?", (slot, h.kernel.clock.today().isoformat()))


async def due(h, slot="s_1200"):
    h.kernel.clock.set_time(slot[2:4] + ":" + slot[4:])
    await ev(h, "dose.due", "scheduler", slot_id=slot, date=h.kernel.clock.today().isoformat())


async def test_due_reminds_then_confirm(kh):
    h = kh
    await due(h)
    assert row(h)["state"] == "reminding"
    assert clips(h) == ["R_s_1200"]
    cards = [u["card"]["label"] for u in faces(h, "card")]
    assert cards[0] == "12:00 食藥" and {"血壓藥", "糖尿藥", "膽固醇藥", "薄血丸"} <= set(cards)
    assert await until(lambda: of(h, "agent.inject"))           # after the clip: reminder session + inject
    inj = of(h, "agent.inject")[0]["payload"]
    assert inj["kind"] == "提醒" and "十二點正" in inj["instruction"] and "血壓藥" in inj["instruction"]
    assert h.kernel.state.session.mode == "reminder"
    assert [e for e in h.kernel.db.query("SELECT payload FROM events WHERE type='arbiter.decision'")]
    r = await req(h, "dose.confirm_request", utterance="食咗喇", source="voice")
    assert r == {"ok": True, "slot_id": "s_1200", "status": "taken_self_reported"}
    assert row(h)["state"] == "taken_self_reported" and row(h)["utterance"] == "食咗喇"
    r = await req(h, "dose.confirm_request", utterance="食咗喇")
    assert r == {"ok": False, "reason": "no_dose_pending"}


async def test_confirm_refused_when_nothing_pending(kh):
    h = kh
    h.kernel.clock.set_time("10:00")
    assert await req(h, "dose.confirm_request", slot_id="s_1200", utterance="食咗") == \
        {"ok": False, "reason": "no_dose_pending"}
    assert row(h) is None
    assert row(h, "s_0800")["state"] == "taken_self_reported"     # seeded


async def test_reprompts_snooze_and_missed(kh):
    h = kh
    await due(h)
    c = h.kernel.clock
    c.advance(15 * 60)
    await ev(h, "schedule.tick", "scheduler")
    assert await until(lambda: len(clips(h)) == 2)
    r = await req(h, "dose.snooze_request", minutes=30)
    assert r["ok"] and r["minutes"] == 15 and row(h)["state"] == "snoozed"
    assert (await req(h, "dose.snooze_request", minutes=10))["ok"]
    assert await req(h, "dose.snooze_request", minutes=10) == {"ok": False, "reason": "max_snoozes"}
    c.advance(11 * 60)
    await ev(h, "schedule.tick", "scheduler")
    assert row(h)["state"] == "reminding"
    c.advance(35 * 60)                                            # T+61
    await ev(h, "schedule.tick", "scheduler")
    assert row(h)["state"] == "missed"
    fam = of(h, "family.send", kind="INFO")
    assert fam and "12:00" in fam[0]["payload"]["text"]
    r = await req(h, "dose.confirm_request", utterance="啱啱食咗")    # MISSED within 3 h still confirmable
    assert r["ok"]


async def test_confirm_window_three_hours(kh):
    h = kh
    await due(h)
    h.kernel.clock.advance(3 * 3600 + 60)
    assert (await req(h, "dose.confirm_request", utterance="食咗"))["reason"] == "no_dose_pending"


async def test_offline_touch_confirmation_and_operator_dose_now(kh):
    h = kh
    h.kernel.clock.set_time("20:05")
    at = h.kernel.clock.now().replace(minute=1).isoformat()
    await ev(h, "device.face_event", "device", event={"type": "reminder_confirmed", "slot_id": "s_2000", "at": at,
                                                      "source": "touch_offline"})
    r = row(h, "s_2000")
    assert r["state"] == "taken_self_reported" and r["source"] == "touch_offline" and r["resolved_at"] == at
    h.kernel.clock.set_time("11:00")
    await ev(h, "operator.dose_now", "operator")
    assert row(h)["state"] == "reminding" and clips(h) == ["R_s_1200"]
    await ev(h, "operator.dose_now", "agent")                     # only the console
    assert len(clips(h)) == 1


async def test_reload_from_db(kh):
    h = kh
    await due(h)
    h.kernel.state.doses = {}
    h.kernel.load()
    assert h.kernel.state.doses["s_1200"]["state"] == "reminding"
    assert (await req(h, "dose.confirm_request", utterance="食咗"))["ok"]
