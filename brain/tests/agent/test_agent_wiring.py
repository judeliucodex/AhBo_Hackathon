"""agent.wiring: dispatchers (deep.start, vision.analyze, agent.* routing), /internal hooks, build_agent."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ahbo.agent import wiring
from ahbo.core.events import Command, Event
from ahbo.kernel.state import SessionState


def make_svc(h):
    return SimpleNamespace(kernel=h.kernel, db=h.kernel.db, clock=h.kernel.clock, config=h.kernel.config,
                           settings=h.kernel.settings, bridge=h.bridge, text_loop=h.loop, spawn=h.kernel.spawn)


async def wait_event(h, type_, timeout=3.0):
    for _ in range(int(timeout / 0.02)):
        row = h.kernel.db.one("SELECT payload FROM events WHERE type=? ORDER BY rowid DESC LIMIT 1", (type_,))
        if row:
            import json
            return json.loads(row["payload"])
        await asyncio.sleep(0.02)
    raise AssertionError(f"no {type_}")


def test_build_agent(harness):
    bridge, loop = wiring.build_agent(harness.kernel)
    assert bridge.kernel is harness.kernel and loop.bridge is bridge
    assert loop.agent.llm.__class__.__name__ == "FakeFlash"


async def test_deep_start_emits_validated_deep_result(harness):
    wiring.install(make_svc(harness))
    await harness.kernel.emit(Command(type="deep.start", correlation_id="sess_x",
                                      payload={"task_id": "deep_t1", "goal": "下晝有咩活動好", "constraints": ""}))
    p = await wait_event(harness, "deep.result")
    assert p["task_id"] == "deep_t1" and p["result"]["status"] == "done"
    assert p["result"]["spoken_summary"].startswith("附近長者中心下晝三點有粵曲班")
    from ahbo.deep.schema import validate_result
    assert validate_result(p["result"]) == []


async def test_deep_runner_failure_becomes_failed_result(harness, monkeypatch):
    class Boom:
        async def run(self, *a, **k):
            raise RuntimeError("x")
    monkeypatch.setattr("ahbo.models.get_deep", lambda **k: Boom())
    wiring.install(make_svc(harness))
    await harness.kernel.emit(Command(type="deep.start", payload={"task_id": "deep_t2", "goal": "g"}))
    p = await wait_event(harness, "deep.result")
    assert p["result"]["status"] == "failed" and p["result"]["spoken_summary"] == "唔好意思，我搵唔到，遲啲再試下好嗎？"


async def test_vision_analyze_emits_advisory_note(harness):
    wiring.install(make_svc(harness))
    await harness.kernel.emit(Command(type="vision.analyze", correlation_id="inc_1",
                                      payload={"incident_id": "inc_1", "photo_id": "ph_1"}))
    p = await wait_event(harness, "vision.result")
    assert p == {"incident_id": "inc_1", "photo_id": "ph_1", "note": "可能有人跌咗喺地下", "ok": True, "advisory": True}


def _livekit_session(h, mode="companion", reason="wakeword"):
    now = h.kernel.clock.now()
    h.kernel.state.session = SessionState(id="sess_lk", reason=reason, mode=mode, text_only=False, opened_at=now,
                                          last_activity=now)


async def test_livekit_commands_go_to_the_pull_queue_and_hooks(harness):
    svc = make_svc(harness)
    wiring.install(svc)
    await harness.kernel.process(Event(type="device.wake", source="device",
                                       payload={"trigger": "wakeword", "s2_text": "喂阿寶今日幾號"}))
    harness.kernel.state.session = None
    _livekit_session(harness)
    k = harness.kernel
    await k.emit(Command(type="session.open", payload={"session_id": "sess_lk", "reason": "wakeword", "mode": "companion"}))
    await k.emit(Command(type="agent.inject", payload={"session_id": "sess_lk", "kind": "提醒", "content": "夠鐘食藥",
                                                       "instruction": "問佢食咗未"}))
    await k.emit(Command(type="agent.mode", payload={"mode": "kernel_listen", "session_id": "sess_lk"}))
    ctx = wiring.internal_context(svc, "sess_lk")
    assert ctx["ok"] and ctx["instructions"].startswith("你係「阿寶」")
    assert ctx["pending_injections"][0]["instructions"] == "[阿寶系統｜提醒｜唔係王婆婆講嘅] 夠鐘食藥。問佢食咗未。"
    items = (await wiring.internal_pull(svc, "sess_lk"))["items"]
    assert [i["kind"] for i in items] == ["inject", "interrupt", "mode"] and items[2]["mode"] == "kernel_listen"
    claim = wiring.internal_claim(svc, {"room": "ahbo-sess_lk"})
    assert claim["ok"] and claim["session_id"] == "sess_lk" and claim["wake"]["s2_text"] == "喂阿寶今日幾號"
    assert wiring.internal_claim(svc, {"room": "ahbo-sess_other"}) == {"ok": False, "reason": "unknown_session"}
    await k.emit(Command(type="session.close", payload={"session_id": "sess_lk"}))
    assert (await wiring.internal_pull(svc, "sess_lk"))["items"] == [{"kind": "close"}]


async def test_pull_long_poll_times_out_empty(harness):
    pq = wiring.PullQueues()
    assert await pq.pull("sess_none", wait_s=0.05) == []


async def test_text_mode_pause_interrupts_the_running_turn(harness):
    wiring.install(make_svc(harness))
    harness.kernel.clock.set_time("09:00")
    await harness.loop.utterance("阿寶，今日有冇人搵過我呀？")
    await asyncio.sleep(0.05)
    await harness.kernel.emit(Command(type="agent.pause", payload={"hard": True}))
    await asyncio.sleep(0.05)
    assert harness.loop.task.done()


async def test_warmup(harness):
    r = await wiring.warmup(make_svc(harness))
    assert r["ok"] and r["context_chars"] > 500


async def test_text_loop_handles_pause_and_resume_itself(harness):
    harness.kernel.clock.set_time("09:00")
    await harness.loop.utterance("阿寶，今日有冇人搵過我呀？")
    await asyncio.sleep(0.05)
    await harness.kernel.emit(Command(type="agent.pause", payload={"hard": True}))
    await asyncio.sleep(0.05)
    assert harness.loop.task.done() and harness.loop.paused
    await harness.kernel.emit(Command(type="agent.resume", payload={"mode": "companion"}))
    assert not harness.loop.paused
