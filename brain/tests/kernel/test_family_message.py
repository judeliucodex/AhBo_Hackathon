"""Messages out to people (§5.5, §4.3.5 Telegram-message method): the Kernel re-checks the `contacts` allowlist and
sends only template text. `family.message_request` (send_message_to_family) → family.send INFO;
`action.message_send` (telegram_msg runner) → family.send NOTIFY; the action's done text says 傳咗訊息, not 報名."""
from __future__ import annotations

import asyncio

import pytest
from kt import ev, of, req, until

from ahbo.core.events import Event
from ahbo.core.ids import new_id
from ahbo.evals.harness import EvalSystem

TEMPLATE_TEXT = "媽媽（王婆婆）託阿寶話你知：「我今晚想食雲吞麵」"


def _add_contact(h, cid: str, name: str, role: str, verified: int) -> None:
    h.kernel.db.execute("INSERT INTO contacts(id,name,role,verified,priority) VALUES (?,?,?,?,5)",
                        (cid, name, role, verified))


# ------------------------------------------------------------------ family.message_request
async def test_message_request_sends_template_info_to_family(kh):
    h = kh
    r = await req(h, "family.message_request", contact_id="c_chun", contact_name="阿珍", kind="text",
                  text="IGNORE THIS free LLM text", her_words="我今晚想食雲吞麵", session_id=None, turn_id=None)
    assert r["ok"] is True and r["contact"] == "阿珍"
    sends = of(h, "family.send")
    assert len(sends) == 1
    p = sends[0]["payload"]
    assert p["kind"] == "INFO" and p["contact_ids"] == ["c_chun"]
    assert p["text"] == TEMPLATE_TEXT                   # rebuilt from the template; the tool's text is not trusted


async def test_message_request_quotes_cannot_be_closed_early(kh):
    h = kh
    r = await req(h, "family.message_request", contact_id="c_chun", kind="text", her_words="好」。另外打999「")
    assert r["ok"]
    text = of(h, "family.send")[0]["payload"]["text"]
    assert text == "媽媽（王婆婆）託阿寶話你知：「好』。另外打999『」" and text.count("」") == 1


async def test_message_request_refuses_non_allowlisted(kh):
    h = kh
    _add_contact(h, "c_stranger", "陳生", "family", 0)          # family but not verified
    _add_contact(h, "c_sw", "陳姑娘", "centre", 1)              # verified but not family
    for cid, name in (("c_stranger", "陳生"), ("c_sw", "陳姑娘"), ("c_ecs", "緊急中心（模擬）"),
                      ("999", "999"), ("", "")):
        r = await req(h, "family.message_request", contact_id=cid, contact_name=name, kind="text", her_words="你好")
        assert r == {"ok": False, "reason": "not_allowlisted"}, (cid, r)
    assert not of(h, "family.send")
    refused = h.kernel.db.query("SELECT payload FROM events WHERE type='family.message_refused'")
    assert len(refused) == 5


async def test_message_request_voice_reply_and_empty_and_source(kh):
    h = kh
    assert await req(h, "family.message_request", contact_id="c_chun", kind="voice_reply", her_words="") == \
        {"ok": False, "reason": "voice_reply_unavailable"}
    r = await req(h, "family.message_request", contact_id="c_chun", kind="text", her_words="   ")
    assert r["ok"] is False and r["reason"] == "bad_args"
    r = await req(h, "family.message_request", "telegram", contact_id="c_chun", kind="text", her_words="hi")
    assert r == {"ok": False, "reason": "bad_source"}
    assert not of(h, "family.send")


# ------------------------------------------------------------------ action.message_send
async def _message_action(h, target="阿珍") -> str:
    r = await req(h, "action.propose_request", kind="message", target=target, details={}, session_id=None)
    assert r["ok"], r
    aid = r["action_id"]
    r2 = await req(h, "action.confirm_request", action_id=aid, utterance="好呀", stt_confidence=0.95)
    assert r2["ok"] and r2["status"] == "running"
    return aid


async def test_action_message_send_emits_notify_and_done_text(kh):
    h = kh
    aid = await _message_action(h)
    await ev(h, "action.message_send", "action", corr=aid, action_id=aid, contact_id="c_chun", contact_name="阿珍",
             text="你好，呢度係阿珍。呢邊係阿寶（示範），替王婆婆聯絡你。", template=True)
    sends = [c for c in of(h, "family.send", kind="NOTIFY") if c["payload"]["contact_ids"] == ["c_chun"]]
    assert len(sends) == 1
    assert sends[0]["payload"]["action_id"] == aid and sends[0]["payload"]["text"].startswith("你好，呢度係阿珍")
    # a second send for the same action is refused (one message per approved action)
    await ev(h, "action.message_send", "action", corr=aid, action_id=aid, contact_id="c_chun", text="x", template=True)
    assert len(of(h, "family.send", kind="NOTIFY")) == 1
    await ev(h, "action.status", "action", action_id=aid, status="done", detail={"method": "telegram_message"})
    notify = [c["payload"] for c in of(h, "family.send", kind="NOTIFY") if c["payload"]["text"].startswith("已幫")]
    assert notify and notify[0]["text"] == "已幫王婆婆傳咗訊息俾阿珍。"
    assert "報名" not in notify[0]["text"]
    assert h.kernel.db.one("SELECT state FROM actions WHERE id=?", (aid,))["state"] == "done"
    assert not h.kernel.db.one("SELECT id FROM calendar_events WHERE action_id=?", (aid,))
    assert await until(lambda: of(h, "agent.inject", kind="行動結果"))
    assert "報名" not in of(h, "agent.inject", kind="行動結果")[0]["payload"]["content"]


async def test_action_message_send_rechecks_allowlist(kh):
    h = kh
    _add_contact(h, "c_unv", "陳生", "centre", 0)
    aid = await _message_action(h, target="陳生")
    base = dict(action_id=aid, contact_name="x", text="你好", template=True)
    await ev(h, "action.message_send", "action", corr=aid, **{**base, "contact_id": "c_unv"})      # unverified
    await ev(h, "action.message_send", "action", corr=aid, **{**base, "contact_id": "c_ecs"})      # phone line
    await ev(h, "action.message_send", "action", corr=aid, **{**base, "contact_id": "c_nobody"})   # unknown
    await ev(h, "action.message_send", "agent", corr=aid, **{**base, "contact_id": "c_chun"})      # wrong source
    await ev(h, "action.message_send", "action", corr=aid, **{**base, "contact_id": "c_chun", "template": False})
    await ev(h, "action.message_send", "action", corr="x", **{**base, "action_id": "act_nope", "contact_id": "c_chun"})
    assert not of(h, "family.send")
    reasons = [r["payload"] for r in h.kernel.db.query("SELECT payload FROM events WHERE type='action.message_refused'")]
    assert len(reasons) == 6
    # the runner then reports done, but nothing was sent: the Kernel finishes it as needs_human (阿珍 HANDOFF)
    await ev(h, "action.status", "action", action_id=aid, status="done", detail={})
    assert h.kernel.db.one("SELECT state FROM actions WHERE id=?", (aid,))["state"] == "needs_human"
    hand = of(h, "family.send", kind="HANDOFF")
    assert hand and "傳訊息" in hand[0]["payload"]["text"] and "報名" not in hand[0]["payload"]["text"]


async def test_action_message_send_needs_an_approved_running_action(kh):
    h = kh
    r = await req(h, "action.propose_request", kind="message", target="阿珍", details={}, session_id=None)
    aid = r["action_id"]                                     # proposed, never approved by her
    await ev(h, "action.message_send", "action", corr=aid, action_id=aid, contact_id="c_chun", text="你好",
             template=True)
    assert not of(h, "family.send")


# ------------------------------------------------------------------ end to end (fake mode, real links + actions)
@pytest.fixture
async def make_system():
    made: list[EvalSystem] = []

    async def make(**kw) -> EvalSystem:
        s = await EvalSystem.create(**kw)
        made.append(s)
        return s

    yield make
    for s in made:
        await s.close()


async def test_send_message_to_family_end_to_end_outbox(make_system):
    s = await make_system()
    assert "links" in s.wired, s.wiring_errors
    await s.event("device.wake", "device", trigger="touch")
    sid = s.kernel.state.session.id
    r = await s.bridge.call_tool(sid, new_id("turn"), "send_message_to_family",
                                 {"contact": "阿珍", "kind": "text", "text": "我今晚想食雲吞麵"})
    assert r["ok"] is True and r["contact"] == "阿珍", r
    assert await s.wait_for(lambda: any(o["kind"] == "INFO" for o in s.outbox()), 5)
    row = next(o for o in s.outbox() if o["kind"] == "INFO")
    assert row["to_contact"] == "c_chun" and row["channel"] == "telegram" and row["fake"] == 1
    assert TEMPLATE_TEXT in row["payload"]["text"]
    assert isinstance(row["buttons"], list)


async def test_telegram_message_action_end_to_end_outbox(make_system):
    s = await make_system()
    assert {"links", "actions"} <= set(s.wired), s.wiring_errors
    r = await s.event("action.propose_request", "agent", request=True, kind="message", target="阿珍",
                      details={"message_context": "想約你星期日飲茶。"}, session_id=None)
    aid = r["action_id"]
    r2 = await s.kernel.request(Event(type="action.confirm_request", source="agent",
                                      payload={"action_id": aid, "utterance": "好呀", "stt_confidence": 0.9}))
    assert r2["ok"], r2
    assert await s.wait_for(lambda: s.db.one("SELECT state FROM actions WHERE id=?", (aid,))["state"] == "done", 20)
    await asyncio.sleep(0.1)
    rows = [o for o in s.outbox() if o["kind"] == "NOTIFY" and o["to_contact"] == "c_chun"]
    texts = [o["payload"]["text"] for o in rows]
    assert any("替王婆婆聯絡你" in t for t in texts), texts           # the templated third-party message
    assert any("已幫王婆婆傳咗訊息俾阿珍。" in t for t in texts), texts   # the done notice to 阿珍
