"""QA22 check 7: agent modes — kernel_listen/paused: no tools, nothing said; reassure: only get_incident_status/
get_time."""
import asyncio
import json
import sys

sys.path.insert(0, "/repo/brain/tests")
from conftest import harness  # noqa: F401,E402


def says(h):
    return [u["text"] for u in h.device.ui() if u.get("type") == "say_start" and u.get("from") != "family"]


def tool_events(h):
    rows = h.kernel.db.query("SELECT payload FROM events WHERE type='agent.tool_call'")
    return [json.loads(r["payload"]) for r in rows]


async def set_mode(h, mode):
    """Force the current session's mode (same path the Kernel uses)."""
    st = h.kernel.state
    if st.session is None:
        await h.say("早晨呀")
        await h.settle(0.3)
    h.kernel.state.session.mode = mode


async def test_kernel_listen_silent(harness):
    h = harness
    await set_mode(h, "kernel_listen")
    n = len(says(h))                                    # the wake line came first
    r = await h.say("阿寶，我幾時食藥呀？")
    await h.settle(0.5)
    assert r.get("consumed") is True, r                 # transcript goes to the Kernel only
    assert len(says(h)) == n, says(h)[n:]                # nothing said
    assert not tool_events(h), tool_events(h)            # no tool runs


async def test_paused_silent(harness):
    h = harness
    await set_mode(h, "paused")
    n = len(says(h))
    r = await h.say("今日天氣點呀？")
    await h.settle(0.5)
    assert r.get("consumed") is True, r
    assert len(says(h)) == n, says(h)[n:]
    assert not tool_events(h), tool_events(h)


async def test_reassure_tool_allowlist(harness):
    h = harness
    await set_mode(h, "reassure")
    sid = h.kernel.state.session.id
    # allowed: get_incident_status + get_time
    ok1 = await h.bridge.call_tool(sid, "t1", "get_incident_status", {})
    ok2 = await h.bridge.call_tool(sid, "t2", "get_time", {})
    assert ok1.get("ok") is True, ok1
    assert ok2.get("ok") is True, ok2
    # everything else refused with reason "mode" (CONTRACTS §6)
    for name, args in (("get_med_status", {}), ("deep_task", {"goal": "x"}), ("recall", {"query": "x"}),
                       ("list_events", {}), ("raise_concern", {"level": "info", "category": "c", "reason": "r"}),
                       ("confirm_taken", {"utterance": "食咗"}), ("get_weather", {})):
        res = await h.bridge.call_tool(sid, "t_" + name, name, args)
        assert res.get("ok") is False and res.get("reason") == "mode", (name, res)


async def _wait_for(pred, timeout_s: float = 30.0):
    import time
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if pred():
            return True
        await asyncio.sleep(0.05)
    return pred()


async def test_fall_flow_modes(harness):
    """The real path: AWAITING_OK runs kernel_listen; after escalation the mode is reassure (§5.1)."""
    h = harness
    h.kernel.clock.set_time("15:00")
    h.kernel.load()
    from ahbo.models.fakes import FAKE_VISION_FINE, FAKE_VISION_KEY
    h.kernel.db.kv_set(FAKE_VISION_KEY, FAKE_VISION_FINE)
    await h.event("sensor.fall_suspected", "beacon", beacon_id="beacon1", peak_g=3.1, freefall_ms=180,
                  stillness_s=2.4)
    await _wait_for(lambda: h.kernel.state.session is not None
                    and h.kernel.state.session.mode == "kernel_listen")
    assert h.kernel.state.session.mode == "kernel_listen"
    sid = h.kernel.state.session.id
    res = await h.bridge.call_tool(sid, "t_meds", "get_med_status", {})
    assert res.get("ok") is False and res.get("reason") == "mode", res
    n_tools = len(tool_events(h))
    r = await h.say("今日天氣點呀？")          # she may still talk during AWAITING_OK
    await h.settle(0.5)
    assert r.get("consumed") is True, r
    assert len(tool_events(h)) == n_tools, tool_events(h)
    # resolve and escalate-less path: OK → RESOLVED_OK; then check reassure on an escalated incident
    await h.say("我冇事", wait=False)
    await _wait_for(lambda: (h.kernel.state.incident or {}).get("state") == "RESOLVED_OK")
    await h.settle(1.0)
    # escalate a new incident via the operator and let the countdown expire → ESCALATING → reassure
    await h.event("sensor.sos_pressed", "beacon", beacon_id="beacon1")
    await _wait_for(lambda: (h.kernel.state.incident or {}).get("state") == "AWAITING_OK")
    await _wait_for(lambda: (h.kernel.state.incident or {}).get("state") in ("ESCALATING", "REASSURING"))
    mode = h.kernel.state.session.mode if h.kernel.state.session else None
    assert mode == "reassure", mode
    sid2 = h.kernel.state.session.id
    ok = await h.bridge.call_tool(sid2, "t_inc", "get_incident_status", {})
    assert ok.get("ok") is True, ok
    no = await h.bridge.call_tool(sid2, "t_meds2", "get_med_status", {})
    assert no.get("ok") is False and no.get("reason") == "mode", no
    # in reassure Ah Bo may speak calm short lines only through the Kernel; her utterances get no chat reply
    n = len(says(h))
    await h.say("阿寶，我幾時食藥呀？")
    await h.settle(0.5)
    assert not [x for x in says(h)[n:] if "兩個鐘之後" in x], says(h)
