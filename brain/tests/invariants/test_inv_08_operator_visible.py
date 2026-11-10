"""INV-8: the operator is visible. Overrides use the same event path as real signals, tagged `source: operator`, and
appear in the trace with what they caused."""
from __future__ import annotations

import pytest

from inv_helpers import fall, need, until_state


async def test_operator_mute_is_an_operator_event_with_its_effects_traced(system):
    s = system
    await s.say("你好呀阿寶")
    await s.event("operator.mute", "operator")
    ev = s.events("operator.mute")
    assert ev and ev[-1]["source"] == "operator"
    mode = [c for c in s.commands("agent.mode") if c["event_id"] == ev[-1]["id"]]
    assert mode and mode[0]["payload"]["mode"] == "paused", "the mute's effect must link to the operator event"


async def test_operator_fall_is_tagged_operator(system):
    s = system
    need(s, "reducer:fall")
    await fall(s, source="operator")
    assert await until_state(s, "ALARM", 10)
    ev = s.events("operator.fall")
    assert ev and ev[0]["source"] == "operator"
    row = s.db.one("SELECT source FROM incidents ORDER BY rowid DESC LIMIT 1")
    assert row and row["source"] == "operator", f"incident must record its operator origin: {row}"


async def test_operator_ok_is_tagged_and_resolves(system):
    s = system
    need(s, "reducer:fall")
    await fall(s)
    assert await until_state(s, "AWAITING_OK")
    await s.event("operator.ok", "operator")
    assert await until_state(s, "RESOLVED_OK", 10), s.incident_path
    assert s.events("operator.ok")[0]["source"] == "operator"


async def test_console_overrides_go_through_operator_events(system):
    demo = pytest.importorskip("ahbo.api.demo", reason="platform console actions (ahbo.api.demo) not built yet")
    s = system
    await s.say("你好呀阿寶")
    r = await demo.override(s.svc, {"kind": "say", "text": "王婆婆，阿珍就嚟到喇。"})
    assert r.get("ok"), r
    r = await demo.override(s.svc, {"kind": "mute"})
    assert r.get("ok"), r
    await s.settle()
    for t in ("operator.say", "operator.mute"):
        ev = s.events(t)
        assert ev and all(e["source"] == "operator" for e in ev), f"{t} not recorded as an operator event"
    says = [c for c in s.commands("device.ui") if (c["payload"].get("ui") or {}).get("type") == "say_start"
            and "阿珍就嚟到" in (c["payload"]["ui"].get("text") or "")]
    assert says, "the operator's line was not sent"
    ids = {e["id"] for e in s.events("operator.say")}
    assert all(c["event_id"] in ids for c in says), \
        "an operator line must be traceable to its operator.say event (not disguised as Ah Bo's own)"
