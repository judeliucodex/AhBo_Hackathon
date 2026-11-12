"""QA21 check 4: concern — every redflag raises, never lowers; emergency joins the fall flow; scam = urgent."""
from __future__ import annotations
import asyncio, sys
sys.path.insert(0, "/repo/brain/tests")
import pytest
from conftest import harness  # noqa: F401
from qa import watch, of, faces, clips, until, ev, req, inc, jsends
from ahbo.core.jsonutil import loads

EMERGENCY = ["胸口痛", "唞唔到氣", "透唔到氣", "手腳冇力", "講嘢唔清楚", "暈低"]
URGENT = ["頭好暈", "好唔舒服", "發燒", "嘔", "想死", "唔想生存", "冇人理我"]
SCAM = ["個孫被捕", "要過數", "銀行戶口", "警察打嚟", "入境處打嚟", "密碼", "驗證碼"]


def decisions(h):
    return [loads(r["payload"]) for r in h.kernel.db.query(
        "SELECT payload FROM events WHERE type='arbiter.decision'")]


@pytest.mark.parametrize("phrase", EMERGENCY)
async def test_emergency_phrase_opens_concern_incident(harness, phrase):
    h = harness; watch(h)
    await h.say(f"阿寶，我{phrase}", wait=False)
    assert await until(lambda: isinstance(inc(h), dict) and inc(h).get("kind") == "concern", 4)
    assert inc(h)["state"] == "AWAITING_OK"
    card = [u["card"] for u in faces(h, "card") if u.get("card", {}).get("kind") == "incident"]
    assert card and card[-1]["label"] == "緊急關注"
    groups = [u["group"] for u in faces(h, "steps")]
    assert groups and groups[-1]["title"] == "緊急處理"
    assert "photo" not in inc(h)["steps"], "emergency concern skips the photo step"
    assert "F3" in clips(h) or await until(lambda: "F3" in clips(h), 2)
    # resolve like a fall: her OK closes it with an INFO
    await h.say("我冇事", wait=False)
    assert await until(lambda: inc(h).get("state") == "RESOLVED_OK", 4)
    h.kernel.cancel_timer(f"fall:clear:{inc(h)['id']}")
    h.kernel.cancel_timer(f"fall:decide:{inc(h)['id']}")


@pytest.mark.parametrize("phrase", URGENT)
async def test_urgent_phrase_notifies_and_offers_call(harness, phrase):
    h = harness; watch(h)
    await h.say(f"阿寶，我{phrase}", wait=False)
    assert await until(lambda: jsends(h), 4)
    sends = jsends(h)
    assert all(s["kind"] == "INFO" for s in sends)
    assert "關心下佢" in sends[-1]["text"]
    assert inc(h) is None, "urgent must NOT open a fall incident"
    assert await until(lambda: of(h, "agent.inject"), 4)   # spoken offer to call 阿珍
    inj = of(h, "agent.inject")[-1]["payload"]
    assert "阿珍" in inj["instruction"] and "唔好診斷" in inj["instruction"]


@pytest.mark.parametrize("phrase", SCAM)
async def test_scam_phrase_is_urgent(harness, phrase):
    h = harness; watch(h)
    await h.say(f"阿寶，{phrase}", wait=False)
    assert await until(lambda: jsends(h), 4)
    sends = jsends(h)
    assert all(s["kind"] == "INFO" for s in sends)
    assert "騙案" in sends[-1]["text"], sends[-1]["text"]
    assert inc(h) is None
    assert await until(lambda: of(h, "agent.inject"), 4)
    inj = of(h, "agent.inject")[-1]["payload"]
    assert "小心" in inj["content"]


async def test_urgent_repeat_suppression(harness):
    h = harness
    await h.say("阿寶，我發燒", wait=False)
    assert await until(lambda: jsends(h), 4)
    await asyncio.sleep(0.6)                             # let the agent's own raise_concern land too
    n = len(jsends(h))
    print("D-REPEAT first utterance sends:", n)          # n==2: matcher(health) + agent(symptom) — defect D?
    await h.say("我仲係好唔舒服", wait=False)
    await asyncio.sleep(0.6)
    assert len(jsends(h)) == n, "same-category urgent within 10 min must not re-send"
    h.kernel.clock.advance(11 * 60)
    await h.say("又發燒喇", wait=False)
    assert await until(lambda: len(jsends(h)) > n, 6), "after 10 min it may send again"


async def test_agent_concern_levels_and_raise_only(harness):
    h = harness
    r = await req(h, "agent.concern", level="urgent", category="health", reason="似係唔舒服")
    assert r == {"ok": True, "level": "urgent"}
    # an LLM info-level concern with an emergency phrase in the reason is raised by the deterministic matcher
    r = await req(h, "agent.concern", level="info", category="health", reason="佢話胸口痛")
    assert r == {"ok": True, "level": "emergency"}, "matcher must raise info → emergency"
    assert inc(h) is not None and inc(h)["kind"] == "concern"


async def test_concern_during_open_incident_never_lowers(harness):
    h = harness
    await h.say("阿寶，我胸口痛", wait=False)                     # emergency → incident AWAITING_OK
    assert await until(lambda: inc(h) is not None, 4)
    st = inc(h)["state"]
    r = await req(h, "agent.concern", level="info", category="other", reason="應該冇嘢")
    assert r == {"ok": True, "level": "emergency"}, "during an incident every concern is at least emergency"
    assert inc(h)["state"] == st, "concern must not change the incident state"
    r2 = await req(h, "agent.concern", level="urgent", category="health", reason="仲係唔舒服")
    assert r2["level"] == "emergency"
    assert not jsends(h), "no extra family sends while the incident is open"
    assert any(x.get("what") == "concern" for x in inc(h)["timeline"])


async def test_utterance_redflag_during_incident_changes_nothing(harness):
    h = harness
    await h.say("阿寶，我胸口痛", wait=False)
    assert await until(lambda: inc(h) is not None, 4)
    n = len(inc(h)["timeline"])
    await h.say("我真係好唔舒服", wait=False)
    await asyncio.sleep(0.2)
    assert inc(h)["state"] == "AWAITING_OK"
    assert not jsends(h)
