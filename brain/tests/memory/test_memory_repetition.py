from __future__ import annotations

from datetime import date

import pytest

from ahbo.memory import repetition as rep


@pytest.mark.parametrize("text,intent", [
    ("我幾點食藥？", "ask_med_time"), ("食咗藥未呀", "ask_med_time"), ("今日有咩安排？", "ask_today"),
    ("今日幾號？", "ask_date"), ("今日星期幾", "ask_date"), ("阿珍幾時嚟探我？", "ask_family_visit"),
    ("我副眼鏡放咗喺邊？", "ask_where_item"), ("今日天氣好好", "other"), ("", "other"),
])
def test_keyword_intent(text, intent):
    assert rep.keyword_intent(text) == intent


async def test_tag_intent_falls_back_without_agent_module():
    assert await rep.tag_intent("我幾點食藥？") == "ask_med_time"


async def test_tag_intent_uses_agent_module(monkeypatch):
    import sys
    import types
    mod = types.ModuleType("ahbo.agent.intent")

    async def tag(text):
        return {"intent": "ask_date"}
    mod.tag = tag
    monkeypatch.setitem(sys.modules, "ahbo.agent.intent", mod)
    assert await rep.tag_intent("隨便") == "ask_date"
    mod.tag = lambda text: "nonsense"          # invalid → keyword fallback
    assert await rep.tag_intent("我幾點食藥？") == "ask_med_time"

    def boom(text):
        raise RuntimeError
    mod.tag = boom
    assert await rep.tag_intent("今日幾號") == "ask_date"


def test_count_and_hint(db, clock):
    assert rep.repetition_hint(db, clock) is None
    for i in range(1, 3):
        assert rep.count(db, clock, "ask_med_time") == i
        assert rep.repetition_hint(db, clock) is None
    assert rep.count(db, clock, "ask_med_time") == 3
    assert rep.repetition_hint(db, clock) == "佢今日第三次問呢個問題，請好似第一次咁耐心答，可以順便講多句安心嘅說話。"
    rep.count(db, clock, "other")
    assert rep.repetition_hint(db, clock) is None          # her latest question is a different one
    rep.count(db, clock, "ask_med_time")
    assert "第四次" in rep.repetition_hint(db, clock)
    clock.advance(86400)
    assert rep.repetition_hint(db, clock) is None          # new day


def test_med_card_from_dose_state(db, clock):
    # 10:00, 08:00 taken (seed) → next is 12:00
    c = rep.answer_card(db, clock, "ask_med_time")
    assert c["id"] == "rep_ask_med_time" and c["kind"] == "info" and c["persistent"] is True
    assert c["label"] == "下次食藥：十二點" and c["small"].startswith("12:00")
    clock.set_time("12:05")
    db.execute("INSERT INTO dose_events(id,slot_id,date,state) VALUES ('d1','s_1200','2026-11-20','reminding')")
    assert rep.answer_card(db, clock, "ask_med_time")["label"] == "而家要食藥"
    db.execute("UPDATE dose_events SET state='taken_self_reported' WHERE id='d1'")
    assert rep.answer_card(db, clock, "ask_med_time")["label"] == "下次食藥：八點"
    clock.set_time("21:00")
    assert rep.answer_card(db, clock, "ask_med_time")["label"] == "今日啲藥食晒喇"


def test_other_cards(db, clock):
    d = rep.answer_card(db, clock, "ask_date")
    assert d["label"] == "今日：十一月二十號" and d["small"] == "星期五"
    t = rep.answer_card(db, clock, "ask_today")
    assert t["label"] == "下一樣：十點半覆診" and "12:30 同阿珍飲茶" in t["small"]
    f = rep.answer_card(db, clock, "ask_family_visit")
    assert f["label"] == "阿珍：今日十二點半"
    assert rep.answer_card(db, clock, "ask_where_item") is None
    clock.set_time("13:00")
    assert rep.answer_card(db, clock, "ask_family_visit") is None     # never invented
    assert rep.answer_card(db, clock, "ask_today")["label"] == "今日冇其他安排"


def test_trend_summary(db):
    rows = [("2026-11-18", "ask_today", 2), ("2026-11-19", "ask_today", 2), ("2026-11-19", "ask_med_time", 1),
            ("2026-11-20", "ask_today", 6), ("2026-11-20", "ask_med_time", 1), ("2026-11-20", "other", 9),
            ("2026-11-01", "ask_today", 50)]
    db.executemany("INSERT INTO repetition(date,intent,count) VALUES (?,?,?)", rows)
    t = rep.trend_summary(db, "2026-11-20")
    assert t["items"][0] == {"intent": "ask_today", "count": 6, "avg": 2, "notable": True}
    assert t["text_en"].startswith("Asked about today's plan 6× (avg 2×)")
    assert t["text"].startswith("問今日安排 6 次（平時平均 2 次）")
    assert all(i["intent"] != "other" for i in t["items"])
    assert rep.trend_summary(db, date(2026, 11, 18))["items"][0]["avg"] is None
    assert rep.trend_summary(db, "2026-12-25")["items"] == []
