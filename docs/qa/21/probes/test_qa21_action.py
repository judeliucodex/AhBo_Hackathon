"""QA21 check 7: external action (§4.3.5, D12/D13) — propose → confirm (confidence, negation) → done/needs_human/failed all tell 阿珍."""
from __future__ import annotations
import asyncio, json, sys
sys.path.insert(0, "/repo/brain/tests")
import pytest
from conftest import harness  # noqa: F401
from qa import watch, of, faces, clips, until, ev, req, timer, jsends

DETAILS = {"title": "長者健體班", "time": "14:30", "venue": "長者中心", "venue_short": "長者中心"}

async def propose(h, details=DETAILS):
    r = await req(h, "action.propose_request", kind="signup", target="act_longer", details=details)
    assert r["ok"], r
    return r

def action_row(h, aid):
    return h.kernel.db.one("SELECT * FROM actions WHERE id=?", (aid,))


async def test_propose_question(harness):
    h = harness; watch(h)
    r = await propose(h)
    assert "報名" in r["question"] and "長者健體班" in r["question"], r["question"]
    assert "2:30" in r["question"] or "兩點半" in r["question"], r["question"]
    a = action_row(h, r["action_id"])
    assert a["state"] == "proposed"


async def test_confirm_low_confidence_asks_again(harness):
    h = harness
    r = await propose(h)
    aid = r["action_id"]
    r2 = await req(h, "action.confirm_request", action_id=aid, utterance="好呀", stt_confidence=0.45)
    assert r2 == {"ok": False, "reason": "low_confidence"}, "yes below 0.6 must ask again (PRD §4.3.5)"
    assert action_row(h, aid)["state"] == "proposed", "still only proposed"
    r3 = await req(h, "action.confirm_request", action_id=aid, utterance="好呀", stt_confidence=0.9)
    assert r3["ok"] and r3["status"] == "running"


async def test_confirm_negation_refuses(harness):
    h = harness
    r = await propose(h)
    aid = r["action_id"]
    r2 = await req(h, "action.confirm_request", action_id=aid, utterance="唔好啦")
    assert r2 == {"ok": False, "reason": "not_affirmative"}
    assert action_row(h, aid)["state"] == "proposed"
    r3 = await req(h, "action.confirm_request", action_id=aid, utterance="")
    assert r3 == {"ok": False, "reason": "not_affirmative"}


async def test_confirm_records_exact_approval(harness):
    h = harness
    r = await propose(h)
    aid = r["action_id"]
    r2 = await req(h, "action.confirm_request", action_id=aid, utterance="好呀，報啦", stt_confidence=0.95)
    assert r2["ok"]
    appr = json.loads(action_row(h, aid)["approval"])
    assert appr["utterance"] == "好呀，報啦" and appr["stt_confidence"] == 0.95 and appr.get("at")
    assert action_row(h, aid)["state"] == "running"
    runs = h.kernel.db.query("SELECT payload FROM commands WHERE type='action.run'")
    assert runs and json.loads(runs[-1]["payload"])["action_id"] == aid, "runner starts only after her yes"


async def test_double_confirm_refused(harness):
    h = harness
    r = await propose(h)
    aid = r["action_id"]
    assert (await req(h, "action.confirm_request", action_id=aid, utterance="好", stt_confidence=1.0))["ok"]
    r2 = await req(h, "action.confirm_request", action_id=aid, utterance="好", stt_confidence=1.0)
    assert r2 == {"ok": False, "reason": "not_proposed"}, "second confirm must refuse"


async def test_done_notifies_chun_books_calendar_tells_popo(harness):
    h = harness; watch(h)
    r = await propose(h)
    aid = r["action_id"]
    await req(h, "action.confirm_request", action_id=aid, utterance="好呀", stt_confidence=1.0)
    await ev(h, "action.status", "action", action_id=aid, status="done",
             detail={"reference": "DEMO-1234"}, screenshots=["/x/pre.png"])
    sends = jsends(h)
    assert sends and sends[-1]["kind"] == "NOTIFY" and "長者健體班" in sends[-1]["text"]
    assert "DEMO-1234" in sends[-1]["text"]
    cal = h.kernel.db.query("SELECT * FROM calendar_events WHERE title='長者健體班'")
    assert cal and cal[-1]["status"] == "booked" and cal[-1]["action_id"] == aid
    assert await until(lambda: of(h, "agent.inject", kind="行動結果"))
    card = [u["card"] for u in faces(h, "card") if u.get("card", {}).get("id") == f"action_{aid}"]
    assert card and card[-1]["label"] == "已報名"
    assert action_row(h, aid)["state"] == "done"
    assert json.loads(action_row(h, aid)["screenshots"]) == ["/x/pre.png"]


async def test_needs_human_sends_handoff(harness):
    h = harness
    r = await propose(h)
    aid = r["action_id"]
    await req(h, "action.confirm_request", action_id=aid, utterance="好呀", stt_confidence=1.0)
    await ev(h, "action.status", "action", action_id=aid, status="needs_human",
             detail={"blocked": "captcha"})
    sends = jsends(h)
    assert sends and sends[-1]["kind"] == "HANDOFF"
    assert action_row(h, aid)["state"] == "needs_human"


async def test_failed_sends_info(harness):
    h = harness
    r = await propose(h)
    aid = r["action_id"]
    await req(h, "action.confirm_request", action_id=aid, utterance="好呀", stt_confidence=1.0)
    await ev(h, "action.status", "action", action_id=aid, status="failed", detail="page not found")
    sends = jsends(h)
    assert sends and sends[-1]["kind"] == "INFO"
    assert action_row(h, aid)["state"] == "failed"


async def test_timeout_fails_and_notifies(harness):
    h = harness
    r = await propose(h)
    aid = r["action_id"]
    await req(h, "action.confirm_request", action_id=aid, utterance="好呀", stt_confidence=1.0)
    await asyncio.sleep(0.1)
    await timer(h, f"action:timeout:{aid}", action_id=aid)
    assert action_row(h, aid)["state"] == "failed"
    sends = jsends(h)
    assert sends and sends[-1]["kind"] == "INFO" and "做唔到" in sends[-1]["text"]


async def test_status_from_unknown_action_ignored(harness):
    h = harness
    await ev(h, "action.status", "action", action_id="act_none", status="done", detail={})
    await asyncio.sleep(0.1)
    assert not jsends(h)
