"""Demo/admin controls from Telegram (reusing ahbo.api.demo; INV-8: fall/SOS carry source operator), the alert
buttons (voice_mum), and the allowlist: a stranger can use no command and no button (INV-7)."""
from __future__ import annotations

import asyncio

import pytest
from bot_helpers import (
    CHUN_CHAT,
    STRANGER,
    button,
    channel,
    commands_of,
    confirm,
    events_of,
    say,
    settle,
    tap,
)

from ahbo.links import outbox, telegram
from ahbo.links.bot import state


async def test_admin_fall_is_an_operator_fall(svc):
    r = await say(svc, "/fall")
    assert "模擬跌倒（操作員觸發）" in r[-1]["text"]
    assert not events_of(svc, "sensor.fall_suspected")          # nothing before the confirm
    done = await confirm(svc, r)
    assert "已模擬跌倒" in done[-1]["text"]
    [ev] = events_of(svc, "sensor.fall_suspected")
    assert ev["source"] == "operator"
    inc = svc.db.one("SELECT source FROM incidents ORDER BY opened_at DESC LIMIT 1")
    assert inc["source"] == "operator"
    ui = " ".join(str(c["payload"]) for c in commands_of(svc, "device.ui"))
    assert "操作員觸發" in ui
    [note] = events_of(svc, "operator.telegram")
    assert note["payload"]["op"] == "fall" and note["payload"]["by"] == "c_chun"


async def test_admin_sos_is_an_operator_sos(svc):
    await confirm(svc, await tap(svc, "m:a:sos"))
    [ev] = events_of(svc, "sensor.sos_pressed")
    assert ev["source"] == "operator"


@pytest.mark.parametrize("cmd,event", [
    ("/mute", "operator.mute"), ("/unmute", "operator.unmute"), ("/plan", "operator.plan_now"),
    ("/panic", "operator.panic"), ("/reset", "operator.reset"), ("/dose s_1200", "operator.dose_now"),
    ("/dose 12:00", "operator.dose_now"), ("/checkin morning", "operator.checkin_now"),
    ("/say 王婆婆，飲啖水先", "operator.say"), ("/thinking flash high", "operator.model_config"),
    ("/thinking sonnet xhigh", "operator.model_config"), ("/clock 07:58", "operator.clock"),
    ("/clock +30", "operator.clock"), ("/clock now", "operator.clock"), ("/clock S2", "operator.clock"),
])
async def test_admin_commands_confirm_then_emit_operator_events(svc, cmd, event):
    r = await say(svc, cmd)
    assert "確認？" in r[-1]["text"]
    assert not events_of(svc, event)
    done = await confirm(svc, r)
    assert done[-1]["text"].startswith("✅"), done[-1]["text"]
    evs = events_of(svc, event)
    assert evs and evs[-1]["source"] == "operator"
    if cmd == "/mute":
        assert (svc.db.kv_get("knobs") or {}).get("muted") is True
    if cmd == "/clock 07:58":
        assert svc.clock.now().strftime("%H:%M") == "07:58"
    if cmd.startswith("/thinking flash"):
        assert svc.db.kv_get("knobs")["flash_thinking"] == "high"
    if cmd.startswith("/dose"):
        assert evs[-1]["payload"]["slot_id"] == "s_1200"


async def test_say_refuses_english_for_popo(svc):
    r = await say(svc, "/say Hello grandma")
    assert "唔好用英文字" in r[-1]["text"] and not state.load(svc.db, CHUN_CHAT).get("pending")


@pytest.mark.parametrize("name", ["S1", "S2", "S3", "S4", "S4ok"])
async def test_scenarios_start_from_telegram(svc, name):
    r = await tap(svc, f"m:a:scen:{name}")
    assert f"行示範場景 {name}" in r[-1]["text"]
    done = await confirm(svc, r)
    assert f"開始行 {name}" in done[-1]["text"]
    task = svc.extra["scenario_task"]
    for _ in range(100):
        if events_of(svc, "operator.reset"):
            break
        await asyncio.sleep(0.02)
    assert events_of(svc, "operator.reset")
    task.cancel()


async def test_bad_admin_args_are_refused(svc):
    for data in ("m:a:flash:ultra", "m:a:ck:night", "m:a:scen:S9", "m:a:dose:nope"):
        r = await tap(svc, data)
        assert "確認？" not in r[-1]["text"], data


# ---------------------------------------------------------------- alert buttons
async def test_voice_mum_button_asks_for_a_voice_note(svc):
    oid = outbox.record(svc.db, ts=svc.clock.iso(), channel="telegram", kind="ALERT", to_contact="c_chun",
                        to_address=CHUN_CHAT, payload={}, buttons=[], fake=True, correlation_id="inc_x")
    for b in ("voice_mum", "call_mum"):
        r = await tap(svc, f"b:{b}:{oid}")
        assert r[-1]["text"] == telegram.VOICE_MUM_REPLY
    await settle(svc)
    assert [e["payload"]["button"] for e in events_of(svc, "family.button")] == ["voice_mum", "voice_mum"]
    assert not [r for r in outbox.outbox_list(svc.db) if r["channel"] == "twilio"]


async def test_handled_and_resolved_buttons_still_work(svc):
    oid = outbox.record(svc.db, ts=svc.clock.iso(), channel="telegram", kind="ALERT", to_contact="c_chun",
                        to_address=CHUN_CHAT, payload={}, buttons=[], fake=True, correlation_id="inc_y")
    await tap(svc, f"b:handled:{oid}")
    await tap(svc, f"b:resolved:{oid}")
    await settle(svc)
    assert [e["payload"]["button"] for e in events_of(svc, "family.button")] == ["handled", "resolved"]


# ---------------------------------------------------------------- strangers (INV-7)
STRANGER_TEXTS = ["/start", "/menu", "/today", "/meds", "/cal", "/settings", "/contacts", "/demo", "/health",
                  "/alerts", "/fall", "/sos", "/reset", "/panic", "/mute", "/scenario S4", "/clock 07:00",
                  "/quiet 01:00 02:00", "/checkintime morning 05:00", "/say 你好", "/thinking flash max",
                  "/dose s_1200", "/fact 婆婆中咗六合彩", "確認", "取消"]
STRANGER_BUTTONS = ["m:menu", "m:v:contacts", "m:v:health", "m:f:contact_add", "m:a:fall", "m:a:reset",
                    "m:a:contact_del:c_chun", "m:a:verify:c_chun", "m:i:_done", "m:ok:deadbeef", "m:x",
                    "b:handled:ob_x", "b:resolved:ob_x", "b:voice_mum:ob_x"]


async def test_a_stranger_can_use_no_command_and_no_button(svc):
    before = svc.db.one("SELECT COUNT(*) n FROM events")["n"]
    for t in STRANGER_TEXTS:
        replies = await say(svc, t, chat=STRANGER)
        assert [r["text"] for r in replies] == [telegram.STOCK_REPLY], t
        assert replies[0]["buttons"] == []
    for d in STRANGER_BUTTONS:
        replies = await tap(svc, d, chat=STRANGER)
        assert replies == [], d                      # only the callback answer (the fixed reply), no message
    await settle(svc)
    assert svc.db.one("SELECT COUNT(*) n FROM events")["n"] == before
    assert svc.db.kv_get(f"bot_convo:{STRANGER}") is None
    assert svc.db.one("SELECT telegram_chat_id FROM contacts WHERE id='c_chun'")["telegram_chat_id"] == CHUN_CHAT
    assert all(r["chat_id"] != STRANGER or r["text"] == telegram.STOCK_REPLY for r in channel(svc).replies)


async def test_a_stranger_cannot_press_the_familys_pending_confirm(svc):
    r = await say(svc, "/fall")
    ok = button(r[-1], "✅")
    await tap(svc, ok, chat=STRANGER)
    await settle(svc)
    assert not events_of(svc, "sensor.fall_suspected")
    assert state.load(svc.db, CHUN_CHAT)["pending"]["op"] == "fall"


async def test_whoami_still_works_for_anyone(svc):
    [r] = await say(svc, "/whoami", chat=STRANGER)
    assert r["text"] == f"你嘅 chat id 係：{STRANGER}"
