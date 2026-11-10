"""INV-1: the LLM talks; the Kernel decides. Tools may *request*; no tool directly performs a consequential action
(phone call, external action run, dose state change) — those only follow a Kernel decision."""
from __future__ import annotations

from ahbo.agent.tools import TOOLS
from ahbo.core.ids import new_id
from inv_helpers import need

# Plausible (and some hostile) arguments per tool; unknown tools get {}.
ARGS = {
    "get_med_status": {"scope": "today"},
    "confirm_taken": {"slot_id": "s_1200", "utterance": "食咗喇"},
    "snooze_dose": {"minutes": 10},
    "list_events": {},
    "add_event": {"datetime": "2026-11-21T15:00:00+08:00", "title": "剪頭髮", "status": "tentative"},
    "get_route": {"destination": "聯合醫院"},
    "find_local_activities": {"when": "afternoon"},
    "check_activity_logistics": {"activity_id": "act_opera_1500"},
    "recall": {"query": "阿珍"},
    "remember": {"fact": "鍾意食叉燒包", "category": "preference"},
    "recall_life_story": {},
    "play_voice_message": {"id": "vm_demo"},
    "send_message_to_family": {"contact": "阿珍", "kind": "text", "text": "媽媽話你好"},
    "deep_task": {"goal": "搵下晝活動", "constraints": ""},
    "request_action": {"kind": "signup", "target": "act_opera_1500", "details": {"activity_id": "act_opera_1500"}},
    "confirm_action": {"action_id": "act_01NOTPROPOSED00000000000000", "utterance": "好呀", "stt_confidence": 0.95},
    "raise_concern": {"level": "info", "category": "mood", "reason": "有啲悶"},
    "get_incident_status": {},
    "show_card": {"card": {"id": "x", "kind": "info", "label": "測試"}},
}


async def test_no_tool_performs_a_consequential_action(system):
    """Calling every tool (as a model would) never places a call or runs an external action by itself, and a dose
    is never marked taken while none is pending (10:00: nothing due)."""
    s = system
    await s.event("device.wake", "device", trigger="touch")
    sid = s.kernel.state.session.id
    for name in sorted(TOOLS):
        res = await s.bridge.call_tool(sid, new_id("turn"), name, ARGS.get(name, {}))
        assert isinstance(res, dict) and "ok" in res, f"{name} returned {res!r} (every result has ok, CONTRACTS §6)"
    await s.settle()
    assert not s.commands("phone.call"), "a tool placed a phone call"
    assert not s.commands("action.run"), "an external action ran without a proposed action + her yes"
    row = s.db.one("SELECT state FROM dose_events WHERE slot_id='s_1200'")
    assert row is None or row["state"] != "taken_self_reported", "a tool marked a dose taken with none pending"
    fam = [c for c in s.commands("family.send")]
    assert all(c["payload"].get("kind") in ("INFO", "NOTIFY", "HANDOFF", None) for c in fam), \
        "a tool sent an ALERT (only the Kernel's incident flow may)"


async def test_tool_results_are_validated_never_raise(system):
    """Invalid arguments → {ok:false, reason:bad_args}; unknown tools → unknown_tool (tool hygiene, §5.5)."""
    s = system
    await s.event("device.wake", "device", trigger="touch")
    sid = s.kernel.state.session.id
    r = await s.bridge.call_tool(sid, new_id("turn"), "no_such_tool", {})
    assert r == {"ok": False, "reason": "unknown_tool"}
    r = await s.bridge.call_tool(sid, new_id("turn"), "get_med_status", {"scope": "everything"})
    assert r == {"ok": False, "reason": "bad_args"}


async def test_confirm_taken_is_a_kernel_request(system):
    s = system
    need(s, "tool:confirm_taken", "reducer:dose")
    await s.event("device.wake", "device", trigger="touch")
    await s.bridge.call_tool(s.kernel.state.session.id, new_id("turn"), "confirm_taken", {"utterance": "食咗喇"})
    assert s.events("dose.confirm_request"), "confirm_taken must go through Kernel.request(dose.confirm_request)"


async def test_external_action_needs_proposal_and_yes(system):
    s = system
    need(s, "tool:request_action", "tool:confirm_action")
    await s.event("device.wake", "device", trigger="touch")
    sid = s.kernel.state.session.id
    r = await s.bridge.call_tool(sid, new_id("turn"), "request_action", ARGS["request_action"])
    assert r.get("ok") and r.get("action_id"), r
    await s.settle()
    assert not s.commands("action.run"), "request_action alone must never start the runner"
    assert s.events("action.propose_request"), "request_action must be a Kernel request (action.propose_request)"
