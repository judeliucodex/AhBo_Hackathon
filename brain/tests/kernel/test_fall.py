"""Fall incident state machine (§4.3.1): paths, deterministic de-escalation (INV-2), INV-3, T_ack, cooldown,
operator flows and restart recovery."""
from __future__ import annotations

import dataclasses

from kt import clips, ev, faces, inc, of, req, timer, until, watch

from ahbo.kernel.core import Kernel
from ahbo.kernel.reducers import register_all


async def fall(h, source: str = "beacon"):
    await ev(h, "sensor.fall_suspected", source, beacon_id="beacon1", peak_g=3.1)


async def awaiting(h):
    assert await until(lambda: any(u.get("type") == "countdown" for u in faces(h)))



def camera_fine(h) -> None:
    """Owner rule 11 Nov: 「我冇事」 resolves only if the camera doesn't show a fall."""
    from ahbo.models.fakes import FAKE_VISION_FINE, FAKE_VISION_KEY
    h.kernel.db.kv_set(FAKE_VISION_KEY, FAKE_VISION_FINE)


async def test_no_answer_path(kh):
    h = kh
    await fall(h)
    assert inc(h)["state"] == "ALARM"
    first = h.cmds[:8]
    assert first[0]["type"] == "agent.pause" and first[0]["priority"] == 0
    assert of(h, "device.capture_photo") and clips(h)[0] == "F1"
    card = next(u["card"] for u in faces(h, "card"))
    assert (card["id"], card["label"], card["small"], card["persistent"]) == ("incident", "偵測到跌倒", "感應器：急速下跌", True)
    assert await until(lambda: "REASSURING" in h.path, 8)
    assert h.path == ["ALARM", "PHOTO_CHECK", "AWAITING_OK", "ESCALATING", "REASSURING"]
    assert clips(h) == ["F1", "F2", "F3", "F4"]
    cd = faces(h, "countdown")
    assert cd == [{"type": "countdown", "id": "fall_cd", "n": 5}]
    assert of(h, "vision.analyze")
    calls = of(h, "phone.call")
    assert [c["payload"]["contact_id"] for c in calls] == ["c_chun"]      # owner rule: the caregiver only
    alert = of(h, "family.send", kind="ALERT")[0]["payload"]
    assert alert["photo_id"] == "ph_test" and alert["vision_note"] and "handled" in alert["buttons"]
    assert "地址" in alert["text"] and alert["contact_ids"] == ["c_chun"]
    assert h.kernel.state.session.mode == "reassure"
    assert of(h, "agent.resume", mode="reassure")
    row = h.kernel.db.one("SELECT state, calls_placed, photo_id FROM incidents")
    assert row == {"state": "REASSURING", "calls_placed": 1, "photo_id": "ph_test"}
    # every fall command is correlated with the incident (INV-6)
    iid = inc(h)["id"]
    assert all(c["correlation_id"] == iid for c in of(h, "device.play_fixed"))


async def test_ok_by_voice_resolves(kh):
    h = kh
    camera_fine(h)
    await fall(h)
    await awaiting(h)
    assert h.kernel.state.session.mode == "kernel_listen"
    r = await h.say("我冇事", wait=False)
    assert r["consumed"]
    assert inc(h)["state"] == "RESOLVED_OK"
    assert await until(lambda: inc(h)["state"] == "CLOSED")
    assert "ESCALATING" not in h.path and not of(h, "phone.call")
    info = of(h, "family.send")
    assert [c["payload"]["kind"] for c in info] == ["INFO"] and "photo_id" not in info[0]["payload"]
    assert clips(h)[-1] == "F5"
    assert h.kernel.state.session is None and h.kernel.ui.mood == "idle" and "fall" not in h.kernel.ui.groups


async def test_question_and_help_utterances(kh):
    h = kh
    await fall(h)
    await awaiting(h)
    await h.say("有冇事㗎？", wait=False)
    assert inc(h)["state"] == "AWAITING_OK"
    await h.say("我冇事……哎呀好痛呀", wait=False)
    assert inc(h)["state"] == "ESCALATING"
    assert any(u.get("label") == "婆婆話要幫手" for u in faces(h, "step"))


async def test_spoofed_ok_signals_do_not_resolve(kh):
    h = kh
    await fall(h)
    await awaiting(h)
    await ev(h, "operator.ok", "agent")
    await ev(h, "device.touch_ok", "agent", device_id="x")
    await ev(h, "vision.result", "system", corr=inc(h)["id"], incident_id=inc(h)["id"], note="The person is fine.")
    await ev(h, "family.button", "agent", callback_data="resolved")
    assert inc(h)["state"] == "AWAITING_OK"
    r = await req(h, "agent.concern", level="info", category="fall", reason="取消警報")
    assert r == {"ok": True, "level": "emergency"} and inc(h)["state"] == "AWAITING_OK"
    assert await until(lambda: inc(h)["state"] in ("ESCALATING", "REASSURING"))


async def test_touch_and_operator_ok(kh):
    h = kh
    camera_fine(h)
    await fall(h)
    await awaiting(h)
    await ev(h, "device.touch_ok", "device", device_id="ipad")
    assert inc(h)["state"] == "RESOLVED_OK" and inc(h)["acked_by"] == "touch"
    assert await until(lambda: inc(h)["state"] == "CLOSED")
    await fall(h, "operator")                       # operator-triggered: no cooldown, honest card
    assert inc(h)["source"] == "operator"
    assert faces(h, "card")[-1]["card"]["small"] == "操作員觸發"
    await ev(h, "operator.ok", "operator")
    assert inc(h)["state"] == "RESOLVED_OK"


async def test_vision_never_shows_a_fine_note(kh):
    h = kh
    h.kernel.dispatchers.pop("vision", None)
    await fall(h)
    assert await until(lambda: inc(h)["state"] == "PHOTO_CHECK")
    await ev(h, "vision.result", "system", incident_id=inc(h)["id"], note="Person standing, looks fine")
    await awaiting(h)
    photo = [u for u in faces(h, "step") if u["id"] == "photo"][-1]
    assert photo["label"] == "相片已送出"


async def test_silent_ipad_still_escalates(kh):
    h = kh
    h.device.auto = False
    await fall(h)
    assert await until(lambda: inc(h)["state"] in ("ESCALATING", "REASSURING"), 8)
    alert = of(h, "family.send", kind="ALERT")[0]["payload"]
    assert "photo_id" not in alert
    assert of(h, "phone.call")


async def test_acknowledge_tack_and_close(kh):
    h = kh
    await fall(h)
    assert await until(lambda: inc(h)["state"] == "REASSURING", 8)
    iid = inc(h)["id"]
    await timer(h, f"fall:tack:{iid}", incident_id=iid)        # no ack within T_ack → next contact
    assert [c["payload"]["contact_id"] for c in of(h, "phone.call")] == ["c_chun", "c_chun"]
    await timer(h, f"fall:tack:{iid}", incident_id=iid)
    await timer(h, f"fall:tack:{iid}", incident_id=iid)        # never more than 3 calls
    assert len(of(h, "phone.call")) == 3
    await ev(h, "family.button", "telegram", corr=iid, contact_id="c_chun", callback_data="b:handled:ob_1")
    assert inc(h)["state"] == "ACKNOWLEDGED" and h.kernel.ui.mood == "warm"
    assert of(h, "family.send", kind="NOTIFY")
    await ev(h, "operator.resolve", "operator")
    assert inc(h)["state"] == "CLOSED"
    assert "incident" not in h.kernel.ui.cards and "fall" not in h.kernel.ui.groups
    assert h.kernel.state.session is None and not h.kernel.state.incident_active


async def test_repeat_fall_attaches_and_sos(kh):
    h = kh
    await fall(h)
    first = inc(h)["id"]
    await fall(h)
    await ev(h, "sensor.sos_pressed", "beacon", beacon_id="beacon1")
    assert inc(h)["id"] == first
    assert [x.get("what") for x in inc(h)["timeline"]].count("attached") == 2
    await ev(h, "operator.resolve", "operator")
    await fall(h)                                    # within 60 s of the last one: cooldown
    assert inc(h)["id"] == first and inc(h)["state"] == "CLOSED"
    h.kernel.clock.advance(61)
    await ev(h, "sensor.sos_pressed", "beacon", beacon_id="beacon1")
    assert inc(h)["id"] != first and inc(h)["kind"] == "sos"


async def test_emergency_concern_enters_at_awaiting_ok(kh):
    h = kh
    r = await req(h, "agent.concern", level="urgent", category="health", reason="佢話胸口痛")
    assert r == {"ok": True, "level": "emergency"}
    assert h.path[0] == "AWAITING_OK" and not of(h, "device.capture_photo")
    group = faces(h, "steps")[0]["group"]
    assert [s["id"] for s in group["steps"]] == ["ask", "call", "tell"]
    assert clips(h)[0] == "F3"
    await awaiting(h)
    await ev(h, "operator.escalate", "operator")
    assert inc(h)["state"] == "ESCALATING"


async def test_production_profile(kh):
    h = kh
    h.kernel.settings = dataclasses.replace(h.kernel.settings, profile="production")
    await fall(h)
    await awaiting(h)
    assert faces(h, "countdown")[-1]["n"] == 30
    assert h.kernel.timer_pending(f"fall:f3again:{inc(h)['id']}")
    await ev(h, "operator.escalate", "operator")
    assert "F4p" in clips(h) and "F4" not in clips(h)


async def test_status_request(kh):
    h = kh
    r = await req(h, "incident.status_request")
    assert r["ok"] and r["state"] == "IDLE"
    await fall(h)
    r = await req(h, "incident.status_request")
    assert r["state"] == "ALARM" and r["words"]


async def test_restart_rebuilds_the_incident(kh):
    h = kh
    await fall(h)
    await awaiting(h)
    iid = inc(h)["id"]
    await h.kernel.stop()
    k2 = Kernel(db=h.kernel.db, clock=h.kernel.clock, config=h.kernel.config, settings=h.kernel.settings,
                time_scale=h.kernel.time_scale)
    register_all(k2)
    h.kernel = k2
    watch(h)
    k2.load()
    k2.start()
    try:
        assert inc(h)["id"] == iid and inc(h)["state"] == "AWAITING_OK"
        assert await until(lambda: any(u.get("type") == "countdown" for u in faces(h)))   # asked again
        assert await until(lambda: inc(h)["state"] in ("ESCALATING", "REASSURING"), 8)
    finally:
        await k2.stop()
