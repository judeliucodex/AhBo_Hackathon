"""Tools (CONTRACTS §6): args validation, mode filter, Kernel requests, memory, weather, cards."""
from __future__ import annotations

import pytest

from ahbo.agent.tools import TOOLS, ToolCtx, load_tools, public, run_tool, schemas
from ahbo.agent.tools import weather
from ahbo.core.config import load_config
from ahbo.core.db import open_db
from ahbo.kernel.core import Kernel
from ahbo.kernel.state import SessionState

ALL = ("get_time", "get_weather", "get_med_status", "confirm_taken", "snooze_dose", "list_events", "add_event",
       "get_day_plan", "get_route", "find_local_activities", "check_activity_logistics", "recall", "remember",
       "recall_life_story", "list_voice_messages", "play_voice_message", "send_message_to_family", "deep_task",
       "request_action", "confirm_action", "raise_concern", "get_incident_status", "show_card")


def test_every_contract_tool_exists_and_has_a_schema():
    load_tools()
    assert set(ALL) <= set(TOOLS)
    names = {s["function"]["name"] for s in schemas("companion")}
    assert "get_incident_status" not in names and "deep_task" in names
    assert {s["function"]["name"] for s in schemas("reassure")} == {"get_time", "get_incident_status"}
    assert schemas("kernel_listen") == [] and schemas("paused") == []


def _set_mode(h, mode):
    now = h.kernel.clock.now()
    h.kernel.state.session = SessionState(id="sess_test", reason="t", mode=mode, text_only=True, opened_at=now,
                                          last_activity=now)


@pytest.mark.parametrize("mode", ["kernel_listen", "paused"])
async def test_mode_filter_blocks_everything(harness, tctx, mode):
    _set_mode(harness, mode)
    for name in ALL:
        assert await run_tool(tctx, name, {}) == {"ok": False, "reason": "mode"}, name


async def test_reassure_allows_only_time_and_incident_status(harness, tctx, stub):
    stub({"incident.status_request": {"ok": True, "state": "ESCALATING", "words": "已經通知緊人嚟幫你"}})
    _set_mode(harness, "reassure")
    assert (await run_tool(tctx, "get_time", {}))["ok"]
    assert (await run_tool(tctx, "get_incident_status", {}))["state"] == "ESCALATING"
    assert await run_tool(tctx, "get_med_status", {}) == {"ok": False, "reason": "mode"}


async def test_incident_status_outside_reassure_is_mode(tctx):
    assert await run_tool(tctx, "get_incident_status", {}) == {"ok": False, "reason": "mode"}


async def test_bad_args(tctx):
    assert (await run_tool(tctx, "snooze_dose", {"minutes": 30}))["reason"] == "bad_args"
    assert (await run_tool(tctx, "raise_concern", {"level": "meh"}))["reason"] == "bad_args"
    assert (await run_tool(tctx, "request_action", {"kind": "pay", "target": "x"}))["reason"] == "bad_args"
    assert (await run_tool(tctx, "nope", {}))["reason"] == "unknown_tool"


async def test_kernel_requests_carry_the_brief_payloads(tctx, stub):
    s = stub({"dose.confirm_request": {"ok": False, "reason": "no_dose_pending"},
              "dose.snooze_request": {"ok": True, "slot_id": "s_1200", "until": "x"},
              "agent.concern": lambda p: {"ok": True, "level": "urgent" if p["category"] == "mood" else p["level"]},
              "action.propose_request": {"ok": True, "action_id": "act_1", "question": "幫唔幫你報名？"},
              "action.confirm_request": {"ok": True, "status": "running"},
              "deep.start_request": {"ok": True, "status": "started", "task_id": "deep_1"}})
    assert await run_tool(tctx, "confirm_taken", {"utterance": "食咗喇"}) == {"ok": False, "reason": "no_dose_pending"}
    assert (await run_tool(tctx, "snooze_dose", {"minutes": 10}))["slot_id"] == "s_1200"
    r = await run_tool(tctx, "raise_concern", {"level": "info", "category": "mood", "reason": "有啲唔開心"})
    assert r == {"ok": True, "level": "urgent"}
    assert (await run_tool(tctx, "request_action", {"kind": "signup", "target": "act_opera_1500"}))["action_id"]
    r = await run_tool(tctx, "confirm_action", {"action_id": "act_1", "utterance": "好呀", "stt_confidence": 0.9})
    assert public(r) == {"ok": True, "status": "running"} and r["_step"]["outcome"] == "pending"
    r = await run_tool(tctx, "deep_task", {"goal": "計劃聽日"})
    assert public(r)["task_id"] == "deep_1" and r["_step"]["outcome"] == "pending"
    types = [t for t, _ in s.seen]
    assert types == ["dose.confirm_request", "dose.snooze_request", "agent.concern", "action.propose_request",
                     "action.confirm_request", "deep.start_request"]
    p = dict(s.seen)
    assert p["dose.confirm_request"]["source"] == "voice" and p["dose.confirm_request"]["utterance"] == "食咗喇"
    assert p["agent.concern"]["session_id"] == "sess_test"
    assert p["action.confirm_request"]["stt_confidence"] == 0.9
    assert p["deep.start_request"]["goal"] == "計劃聽日" and p["deep.start_request"]["constraints"] == ""


async def test_without_a_reducer_kernel_tools_are_unavailable():
    from ahbo.core.clock import FakeClock
    from ahbo.core.settings import load_settings
    db = open_db(":memory:")
    k = Kernel(db=db, clock=FakeClock("2026-11-20T10:00:00+08:00"), config=load_config(), settings=load_settings())
    ctx = ToolCtx(kernel=k, session_id=None)
    for name, args in (("confirm_taken", {"utterance": "x"}), ("snooze_dose", {"minutes": 5}),
                       ("raise_concern", {"level": "urgent", "category": "x", "reason": "y"}),
                       ("deep_task", {"goal": "g"}), ("request_action", {"kind": "signup", "target": "t"}),
                       ("confirm_action", {"action_id": "a", "utterance": "好"})):
        r = public(await run_tool(ctx, name, args))
        assert r["ok"] is False and r["reason"] == "unavailable", name
    db.close()


async def test_send_message_to_family(tctx, stub):
    s = stub({"family.message_request": {"ok": True}})
    r = await run_tool(tctx, "send_message_to_family", {"contact": "阿珍", "kind": "text", "text": "我今晚想食雲吞麵"})
    assert public(r) == {"ok": True, "contact": "阿珍"}
    p = s.seen[0][1]
    assert p["contact_id"] == "c_chun" and p["text"] == "媽媽（王婆婆）託阿寶話你知：「我今晚想食雲吞麵」"
    for bad in ("陳生", "999", "c_ecs"):          # not a verified family contact → refused before the Kernel
        r = await run_tool(tctx, "send_message_to_family", {"contact": bad, "kind": "text", "text": "x"})
        assert r["ok"] is False and r["reason"] == "unknown_contact"
    assert len(s.seen) == 1


async def test_memory_tools(harness, tctx):
    r = await run_tool(tctx, "recall", {"query": "我琴日同你講咗咩？"})
    assert r["ok"] and any("膝頭" in e["summary"] for e in r["episodes"])
    r = await run_tool(tctx, "remember", {"fact": "鍾意食叉燒包", "category": "preference"})
    assert r["ok"] and r["status"] == "active"
    r = await run_tool(tctx, "remember", {"fact": "身份證號碼係A1234567", "category": "other"})
    assert r == {"ok": False, "reason": "pii"}
    r = await run_tool(tctx, "remember", {"fact": "有糖尿病", "category": "health"})
    assert r["status"] == "pending_review"
    r = await run_tool(tctx, "recall_life_story", {})
    assert r["ok"] and r["story"]["title"] and r["_cards"][0]["kind"] == "story"
    used = harness.kernel.db.one("SELECT last_used_at FROM life_stories WHERE id=?", (r["story"]["id"],))
    assert used["last_used_at"]


async def test_weather_fake_and_hko_summary(tctx):
    r = await run_tool(tctx, "get_weather", {})
    assert r["ok"] and r["source"] == "fake" and len(r["weather_short"]) <= 14
    assert r["_step"]["vars"]["weather_short"] == r["weather_short"]
    rhr = {"temperature": {"data": [{"place": "京士柏", "value": 21}, {"place": "觀塘", "value": 22.6}]}, "icon": [62]}
    flw = {"forecastDesc": "大致多雲，有一兩陣微雨。最高氣溫25度。"}
    s = weather.summarise(rhr, flw)
    assert s["temp_c"] == 23 and s["now_words"] == "而家二十三度，微雨" and s["rain_likely"]
    assert s["weather_short"] == "二十三度，微雨" and s["source"] == "hko"
    assert len(weather.short_words(18, "短暫時間有陽光，有驟雨")) <= 14


async def test_weather_network_failure_falls_back(monkeypatch):
    class S:
        def is_fake(self, _):
            return False

    async def boom(*a, **k):
        raise OSError("down")

    weather.clear_cache()
    monkeypatch.setattr(weather, "fetch_hko", boom)
    assert (await weather.current(S()))["source"] == "fake"


async def test_add_event_and_show_card(harness, tctx):
    r = await run_tool(tctx, "add_event", {"datetime": "2026-11-21T15:00:00", "title": "剪頭髮", "status": "confirmed"})
    assert r["ok"] and r["_cards"][0]["label"] == "3:00 剪頭髮" and r["_cards"][0]["small"] == "聽日"
    row = harness.kernel.db.one("SELECT * FROM calendar_events WHERE id=?", (r["id"],))
    assert row["datetime"].startswith("2026-11-21T15:00") and row["source"] == "agent"
    assert (await run_tool(tctx, "add_event", {"datetime": "聽日", "title": "x"}))["reason"] == "bad_datetime"
    r = await run_tool(tctx, "show_card", {"card": {"kind": "info", "label": "記得帶遮"}})
    assert r["ok"] and r["_cards"][0]["id"].startswith("show_")
    assert (await run_tool(tctx, "show_card", {"card": {"id": "incident", "kind": "info", "label": "x"}}))["reason"] \
        == "reserved"
    assert (await run_tool(tctx, "show_card", {"card": {"kind": "incident", "label": "x"}}))["reason"] == "bad_args"


async def test_quality_events_are_kernel_events(harness):
    from ahbo.agent.runner import TurnAgent
    from ahbo.core.adapters import LLMResponse

    class Digits:
        async def chat(self, *a, **k):
            return LLMResponse(text="你下一次食藥係15:00，有4粒。")

    await harness.say("你好呀阿寶")
    sid = harness.kernel.state.session.id
    await TurnAgent(harness.bridge, Digits()).run_turn(sid, "turn_q", "幾時食藥")
    await harness.bridge.user_turn(sid, "turn_q2", "唔清楚", stt_confidence=0.3)
    types = {r["type"] for r in harness.kernel.db.query("SELECT type FROM events WHERE type LIKE 'quality.%'")}
    assert {"quality.digits_spoken", "quality.stt_low_confidence"} <= types
    said = harness.kernel.db.one("SELECT text FROM turns WHERE role='ahbo' ORDER BY rowid DESC LIMIT 1")["text"]
    assert said == "你下一次食藥係下晝三點，有四粒。"


async def test_mode_without_session_follows_the_incident(harness, tctx):
    k = harness.kernel
    k.state.session = None
    k.state.incident = {"state": "AWAITING_OK"}
    assert tctx.mode == "kernel_listen" and (await run_tool(tctx, "get_time", {}))["reason"] == "mode"
    k.state.incident = {"state": "ESCALATING"}
    assert tctx.mode == "reassure" and (await run_tool(tctx, "get_time", {}))["ok"]
    k.state.incident = None
    assert tctx.mode == "companion"


async def test_reassure_mode_is_not_consumed_during_an_incident(harness):
    _set_mode(harness, "reassure")
    harness.kernel.state.incident = {"state": "REASSURING"}
    r = await harness.bridge.user_turn("sess_test", "turn_r", "救護車嚟咗未呀？")
    assert r["consumed"] is False and r["mode"] == "reassure"
    _set_mode(harness, "kernel_listen")
    assert (await harness.bridge.user_turn("sess_test", "turn_k", "我冇事"))["consumed"] is True
