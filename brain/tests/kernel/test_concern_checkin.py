"""Concern (§4.3.4) and check-ins (§4.3.3)."""
from __future__ import annotations

from kt import ev, faces, inc, of, req, until


async def test_concern_levels(kh):
    h = kh
    await h.say("你好呀阿寶")
    sid = h.kernel.state.session.id
    r = await req(h, "agent.concern", level="info", category="mood", reason="有啲悶", session_id=sid)
    assert r == {"ok": True, "level": "info"}
    assert h.kernel.db.kv_get("concern_digest")["items"][0]["category"] == "mood"
    r = await req(h, "agent.concern", level="info", category="health", reason="佢話頭好暈", session_id=sid)
    assert r["level"] == "urgent"                     # raised by the red-flag matcher, never lowered
    fam = of(h, "family.send", kind="INFO")
    assert len(fam) == 1 and fam[0]["payload"]["contact_ids"] == ["c_chun"]
    assert await until(lambda: of(h, "agent.inject", kind="家人"))
    await req(h, "agent.concern", level="urgent", category="health", reason="頭好暈", session_id=sid)
    assert len(of(h, "family.send", kind="INFO")) == 1   # once per category per 10 min
    assert h.kernel.state.incident is None


async def test_redflag_on_utterance(kh):
    h = kh
    r = await h.say("我胸口痛呀", wait=False)
    assert r["consumed"]                              # the incident took the floor; Flash does not reply
    assert inc(h)["state"] == "AWAITING_OK" and inc(h)["kind"] == "concern"
    assert not of(h, "device.capture_photo")


async def test_scam_is_urgent(kh):
    h = kh
    await h.say("有人打嚟話個孫被捕，要過數", wait=True)
    fam = of(h, "family.send", kind="INFO")
    assert fam and "騙案" in fam[0]["payload"]["text"]


async def test_checkin_delivery_and_no_reply(kh):
    h = kh
    h.kernel.clock.set_time("15:00")
    await ev(h, "checkin.due", "scheduler", kind="afternoon")
    assert h.kernel.state.session is not None and h.kernel.state.session.reason == "checkin_afternoon"
    assert faces(h, "mood")[0]["mood"] == "curious"
    inj = of(h, "agent.inject", kind="問候")[0]["payload"]
    assert inj["priority"] == 3 and "下晝" in inj["content"]
    assert await until(lambda: h.kernel.state.session is None, 5)      # 20 s without a reply → closed
    assert any(c["payload"].get("why") == "checkin_no_reply" for c in of(h, "session.close"))


async def test_checkin_policy(kh):
    h = kh
    h.kernel.clock.set_time("23:00")
    await ev(h, "checkin.due", "scheduler", kind="evening")
    assert not of(h, "agent.inject")                  # quiet hours
    h.kernel.clock.set_time("08:30")
    await h.say("你好呀阿寶")
    await ev(h, "checkin.due", "scheduler", kind="morning")
    assert "morning" in h.kernel.state.checkins["deferred"]   # session open → deferred
    await ev(h, "session.closed", "agent", session_id=h.kernel.state.session.id)
    await ev(h, "schedule.tick", "scheduler")
    assert not of(h, "agent.inject", kind="問候")      # session ended < 20 min ago → skipped
    assert not h.kernel.state.checkins["deferred"]
    await ev(h, "operator.checkin_now", "operator", checkin="morning")   # the console forces it
    assert of(h, "agent.inject", kind="問候")
