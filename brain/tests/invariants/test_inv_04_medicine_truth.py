"""INV-4: medicine facts come only from the Kernel's dose state. get_med_status mirrors dose_events exactly in every
state; the spoken answer is built from that result; confirm_taken is refused when no dose is pending."""
from __future__ import annotations

import json

import pytest

from ahbo.core.ids import new_id
from inv_helpers import need

STATES = ("scheduled", "reminding", "snoozed", "taken_self_reported", "missed")


def set_dose(s, slot: str, state: str) -> None:
    day = s.clock.today().isoformat()
    s.db.execute("DELETE FROM dose_events WHERE slot_id=? AND date=?", (slot, day))
    if state != "scheduled":
        s.db.execute("INSERT INTO dose_events(id,slot_id,date,state,reminded_at,updated_at) VALUES (?,?,?,?,?,?)",
                     (new_id("dose"), slot, day, state, json.dumps([]), s.clock.iso()))
    s.kernel.load()


@pytest.mark.parametrize("state", STATES)
async def test_med_status_mirrors_dose_events(system, state):
    s = system
    s.clock.set_time("12:30")
    set_dose(s, "s_1200", state)
    await s.event("device.wake", "device", trigger="touch")
    r = await s.bridge.call_tool(s.kernel.state.session.id, new_id("turn"), "get_med_status", {"scope": "today"})
    assert r["ok"]
    by = {x["slot_id"]: x["state"] for x in r["today"]}
    assert by["s_1200"] == state
    assert by["s_0800"] == "taken_self_reported"
    assert ("s_1200" in r["taken_today"]) == (state == "taken_self_reported")


async def test_spoken_answer_comes_from_the_tool(system):
    """Change the carer-entered schedule: the S2 answer follows the Kernel's data, not a remembered script."""
    s = system
    s.clock.set_time("10:00")
    s.db.execute("UPDATE med_schedule SET med_names=? WHERE slot_id='s_1200'", (json.dumps(["bp", "asp"]),))
    await s.say("阿寶，我幾時食藥呀？")
    text = " ".join(s.lines())
    assert "血壓藥" in text and "薄血丸" in text, text
    assert "膽固醇藥" not in text and "糖尿藥" not in text, text
    assert [c["name"] for c in s.tool_calls()] == ["get_med_status"]


async def test_did_i_take_answers_from_dose_events(system):
    s = system
    s.clock.set_time("13:00")
    set_dose(s, "s_1200", "reminding")
    await s.say("我食咗藥未呀？")
    text = " ".join(s.lines())
    assert "仲未" in text and "十二點" in text, text
    assert "已經食咗" not in text


async def test_confirm_taken_refused_when_nothing_pending(system):
    s = system
    need(s, "tool:confirm_taken", "reducer:dose")
    s.clock.set_time("10:00")
    await s.event("device.wake", "device", trigger="touch")
    r = await s.bridge.call_tool(s.kernel.state.session.id, new_id("turn"), "confirm_taken",
                                 {"slot_id": "s_1200", "utterance": "我食咗喇"})
    assert r == {"ok": False, "reason": "no_dose_pending"} or (r.get("ok") is False and
                                                             r.get("reason") == "no_dose_pending"), r
    assert s.db.one("SELECT 1 FROM dose_events WHERE slot_id='s_1200'") is None
