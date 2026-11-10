"""INV-6: everything is an event; everything is traced. Every command links to a recorded event and carries a
correlation id; every tool call has a recorded call and result with the same call id; events are append-only."""
from __future__ import annotations

import sqlite3

import pytest

from inv_helpers import fall, need, until_state


def assert_traced(s) -> None:
    events = {e["id"]: e for e in s.events()}
    for e in events.values():
        assert e["id"].startswith("evt_") and e["ts"] and e["source"] and e["type"], e
    bad_link = [(c["type"], c["event_id"]) for c in s.commands() if not c["event_id"] or c["event_id"] not in events]
    assert not bad_link, f"commands without a recorded event: {bad_link[:10]}"
    no_corr = sorted({(c["type"], events[c["event_id"]]["type"]) for c in s.commands() if not c["correlation_id"]})
    assert not no_corr, f"commands without a correlation id (command, from event): {no_corr}"
    calls = {e["payload"]["call_id"]: e for e in s.events("agent.tool_call")}
    results = {e["payload"]["call_id"]: e for e in s.events("agent.tool_result")}
    assert set(calls) == set(results), "every tool call needs a recorded result with the same call id"
    for cid, e in calls.items():
        assert e["correlation_id"], f"tool call {cid} has no correlation id"


async def test_conversation_is_fully_traced(system):
    s = system
    s.clock.set_time("09:00")
    await s.say("阿寶，今日有冇人搵過我呀？")
    await s.wait(6)
    s.clock.set_time("10:00")
    await s.say("阿寶，我幾時食藥呀？")
    await s.settle()
    assert s.tool_calls(), "no tool calls recorded"
    assert_traced(s)


async def test_repeated_questions_are_fully_traced(system):
    """Components that react to turns (e.g. the repetition card, §7.4) must also leave a trace."""
    s = system
    s.clock.set_time("10:00")
    for _ in range(3):
        await s.say("我幾時食藥呀？")
    await s.wait(2)
    assert_traced(s)


async def test_fall_is_fully_traced(system):
    s = system
    need(s, "reducer:fall")
    await fall(s)
    assert await until_state(s, "ESCALATING", 60)
    await s.wait(6)
    assert_traced(s)
    iid = s.kernel.state.incident
    iid = iid.get("id") if isinstance(iid, dict) else getattr(iid, "id", None)
    assert iid and s.db.query("SELECT 1 FROM commands WHERE correlation_id=?", (iid,)), \
        "fall commands must be correlated with the incident id"


async def test_event_log_is_append_only(system):
    s = system
    await s.say("你好呀阿寶")
    with pytest.raises(sqlite3.DatabaseError):
        s.db.execute("UPDATE events SET type='x'")
