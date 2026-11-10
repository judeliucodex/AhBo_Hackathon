from __future__ import annotations

import json

import pytest

from ahbo.core.adapters import LLMResponse
from ahbo.core.ids import new_id
from ahbo.core.jsonutil import loads
from ahbo.memory import extractor


class ScriptedFlash:
    def __init__(self, text=None, exc=None):
        self.text, self.exc, self.calls = text, exc, []

    async def chat(self, messages, tools=None, *, purpose="voice_turn", thinking=None, correlation_id=None):
        self.calls.append({"messages": messages, "purpose": purpose})
        if self.exc:
            raise self.exc
        return LLMResponse(text=self.text if isinstance(self.text, str) else json.dumps(self.text, ensure_ascii=False))


def make_session(db, clock, lines):
    sid = new_id("sess")
    db.execute("INSERT INTO sessions(id, opened_at, reason, mode) VALUES (?,?,?,?)", (sid, clock.iso(), "touch", "companion"))
    for role, text in lines:
        db.execute("INSERT INTO turns(id, session_id, ts, role, text) VALUES (?,?,?,?,?)",
                   (new_id("t"), sid, clock.iso(), role, text))
    return sid


GOOD = {"summary": ["佢話今朝去咗公園散步。", "傾咗阿明生日。", "心情幾好。"], "mood": "happy", "topics": ["散步", "阿明"],
        "facts": [
            {"fact": "鍾意朝早去公園散步", "category": "routine", "confidence": 0.9},
            {"fact": "鍾意食燒賣", "category": "preference", "confidence": 0.6},          # too unsure: dropped
            {"fact": "成日同樓下陳太傾偈", "category": "social", "confidence": 0.85},
            {"fact": "最近膝頭痛到要食止痛藥", "category": "health", "confidence": 0.9},   # → pending
            {"fact": "阿珍係我新抱", "category": "family", "confidence": 0.95},           # → pending
            {"fact": "我電話係91234567", "category": "other", "confidence": 1.0},          # PII: dropped
            {"fact": "鍾意每日食三粒血壓藥", "category": "routine", "confidence": 0.95},   # medicine: forced pending
            {"fact": "以前住喺深水埗", "category": "life", "confidence": 0.9},             # not auto category
            {"fact": 123, "category": "routine", "confidence": 0.9},                     # bad item ignored
        ]}


async def test_extractor_policy(db, clock):
    sid = make_session(db, clock, [("user", "我今朝去咗公園散步，我電話係91234567"), ("ahbo", "好呀！")])
    flash = ScriptedFlash(GOOD)
    res = await extractor.run_extractor(db, clock, sid, flash=flash)
    assert res["ok"] and flash.calls[0]["purpose"] == "extractor"
    prompt = flash.calls[0]["messages"][-1]["content"]
    assert "91234567" not in prompt and "〔已隱藏〕" in prompt          # transcript redacted before the model
    s = db.one("SELECT * FROM sessions WHERE id=?", (sid,))
    assert loads(s["summary"]) == GOOD["summary"] and s["summary_text"].startswith("佢話今朝")
    assert s["mood"] == "happy" and loads(s["topics"]) == ["散步", "阿明"]
    st = {r["fact"]: r["status"] for r in db.query("SELECT fact, status FROM facts WHERE source='extractor'")}
    assert st == {"鍾意朝早去公園散步": "active", "成日同樓下陳太傾偈": "active",
                  "最近膝頭痛到要食止痛藥": "pending_review", "阿珍係我新抱": "pending_review",
                  "鍾意每日食三粒血壓藥": "pending_review"}
    assert len(res["facts"]["active"]) == 2 and len(res["facts"]["pending_review"]) == 3
    assert "pii" in res["facts"]["dropped"]
    # recall finds the new summary
    from ahbo.memory import store
    assert store.recall(db, "公園散步")["episodes"][0]["session_id"] == sid
    # idempotent
    again = await extractor.run_extractor(db, clock, sid, flash=flash)
    assert again.get("skipped") and len(flash.calls) == 1


async def test_supersedes_family_fact_goes_to_review(db, clock):
    old = db.one("SELECT id FROM facts WHERE fact LIKE '女兒叫阿珍%'")["id"]
    pref = db.one("SELECT id FROM facts WHERE fact LIKE '鍾意飲茶%'")["id"]
    sid = make_session(db, clock, [("user", "阿珍唔係我女，係我新抱。我而家唔鍾意飲茶，鍾意飲咖啡")])
    out = {"summary": ["佢講起阿珍。"], "facts": [
        {"fact": "阿珍係新抱", "category": "family", "confidence": 0.9, "supersedes": old},
        {"fact": "鍾意飲咖啡多過飲茶", "category": "preference", "confidence": 0.9, "supersedes": pref},
        {"fact": "鍾意影相", "category": "preference", "confidence": 0.9, "supersedes": "fact_does_not_exist"}]}
    await extractor.run_extractor(db, clock, sid, flash=ScriptedFlash(out))
    assert db.one("SELECT status FROM facts WHERE id=?", (old,))["status"] == "active"      # not overwritten
    assert db.one("SELECT status FROM facts WHERE supersedes=?", (old,))["status"] == "pending_review"
    assert db.one("SELECT status FROM facts WHERE id=?", (pref,))["status"] == "superseded"
    assert db.one("SELECT status FROM facts WHERE fact='鍾意影相'")["status"] == "active"


@pytest.mark.parametrize("bad", [
    "", "not json", "```json\n{broken", "[]", '"a string"', "{}", '{"summary": []}', '{"summary": 5}',
    '{"summary": null, "facts": [{"fact": "x"}]}', "{" * 5000, "null",
])
async def test_malformed_output_never_raises(db, clock, bad):
    sid = make_session(db, clock, [("user", "早晨")])
    res = await extractor.run_extractor(db, clock, sid, flash=ScriptedFlash(bad))
    assert res["ok"] is False and res["reason"] == "invalid_output"
    assert db.one("SELECT summary_text FROM sessions WHERE id=?", (sid,))["summary_text"] == ""
    assert db.one("SELECT COUNT(*) n FROM quality WHERE kind='memory.extractor_invalid'")["n"] == 1


async def test_tolerant_parse():
    ex = extractor.parse_output('好嘅：\n```json\n{"summary": "第一句\\n第二句", "mood": "SAD", "topics": "x",'
                                ' "facts": "none", "extra": 1}\n```')
    assert ex.summary == ["第一句", "第二句"] and ex.mood is None and ex.topics == [] and ex.facts == []
    ex = extractor.parse_output(json.dumps({"summary": [f"句{i}" for i in range(9)],
                                            "facts": [{"fact": "鍾意", "confidence": "high"}]}))
    assert len(ex.summary) == 5 and ex.facts[0].confidence == 0.0


async def test_model_error_and_no_turns(db, clock):
    sid = make_session(db, clock, [("user", "早晨")])
    res = await extractor.run_extractor(db, clock, sid, flash=ScriptedFlash(exc=RuntimeError("down")))
    assert res == {"ok": False, "session_id": sid, "reason": "model_error"}
    empty = make_session(db, clock, [("ahbo", "王婆婆早晨")])
    flash = ScriptedFlash(GOOD)
    res = await extractor.run_extractor(db, clock, empty, flash=flash)
    assert res["skipped"] and not flash.calls
    assert (await extractor.run_extractor(db, clock, "sess_nope", flash=flash))["reason"] == "no_session"


async def test_fake_mode_default_flash(db, clock):
    """No flash passed: uses ahbo.models.get_flash() (FakeFlash under AHBO_FAKE=all)."""
    sid = make_session(db, clock, [("user", "今日天氣好好")])
    res = await extractor.run_extractor(db, clock, sid)
    assert res["ok"], res
    assert db.one("SELECT summary_text FROM sessions WHERE id=?", (sid,))["summary_text"]


async def test_summary_pii_redacted(db, clock):
    sid = make_session(db, clock, [("user", "我身份證A123456(7)")])
    await extractor.run_extractor(db, clock, sid, flash=ScriptedFlash({"summary": ["佢講咗身份證A123456(7)。"],
                                                                     "topics": ["A123456(7)", "身份證"]}))
    s = db.one("SELECT * FROM sessions WHERE id=?", (sid,))
    assert "A123456" not in s["summary"] and "A123456" not in s["summary_text"] and "A123456" not in s["topics"]
