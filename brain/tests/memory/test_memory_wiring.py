"""Wiring against the real Kernel (harness fixture): session close → extractor, utterances → repetition cards."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from ahbo.core.events import Command, Event
from ahbo.memory import repetition, wiring


def svc_for(h):
    k = h.kernel
    return SimpleNamespace(kernel=k, db=k.db, clock=k.clock, config=k.config, settings=k.settings,
                           spawn=lambda coro: k.spawn(coro, name="test"))


async def wait_for(pred, timeout=3.0):
    t = 0.0
    while t < timeout:
        if pred():
            return True
        await asyncio.sleep(0.02)
        t += 0.02
    return pred()


def rep_cards(h):
    return [u["card"] for u in h.device.ui() if u.get("type") == "card" and u["card"]["id"].startswith("rep_")]


async def test_repeated_question_card_and_hint(harness):
    h = harness
    wiring.install(svc_for(h))
    db, clock = h.kernel.db, h.kernel.clock
    for i in range(1, 4):
        await h.say("我幾點食藥？")
        assert await wait_for(lambda i=i: repetition.get_count(db, clock.today().isoformat(), "ask_med_time") == i)
    assert await wait_for(lambda: bool(rep_cards(h)))
    card = rep_cards(h)[-1]
    assert card == {"id": "rep_ask_med_time", "kind": "info", "persistent": True, "label": "下次食藥：十二點",
                    "small": "12:00 · 血壓藥、糖尿藥、膽固醇藥、薄血丸"}
    assert "rep_ask_med_time" in h.kernel.ui.cards
    assert "第三次" in repetition.repetition_hint(db, clock)
    # a new day: the tick clears the stale card and runs retention once
    clock.advance(86400)
    await h.event("schedule.tick", source="scheduler", now=clock.iso())
    assert await wait_for(lambda: "rep_ask_med_time" not in h.kernel.ui.cards)
    assert await wait_for(lambda: db.kv_get(wiring.RETENTION_KEY) == clock.today().isoformat())


async def test_session_close_runs_extractor(harness):
    h = harness
    wiring.install(svc_for(h))
    db = h.kernel.db
    await h.say("你好")
    sid = h.kernel.state.session.id
    await h.event("session.closed", source="agent", session_id=sid)
    assert await wait_for(lambda: db.one("SELECT 1 FROM events WHERE type='memory.extracted'") is not None)
    ev = db.one("SELECT payload FROM events WHERE type='memory.extracted'")
    assert sid in ev["payload"] and '"ok":true' in ev["payload"]
    assert db.one("SELECT summary_text FROM sessions WHERE id=?", (sid,))["summary_text"]
    assert any(c["type"] == "memory.session_closed" for c in db.query("SELECT type FROM commands"))


async def test_memory_write_command(harness):
    h = harness
    wiring.install(svc_for(h))
    db = h.kernel.db
    await h.kernel.emit(Command(type="memory.write", payload={"op": "add_fact", "fact": "鍾意打麻雀",
                                                               "category": "preference", "source": "telegram"}))
    await h.kernel.emit(Command(type="memory.write", payload={"op": "add_fact", "fact": "密碼係1234",
                                                               "category": "other"}))
    await h.kernel.emit(Command(type="memory.write", payload={"op": "add_story", "text": "細個喺長洲住過。",
                                                               "photo_id": "ph_9"}))
    await h.kernel.emit(Command(type="memory.write", payload={"op": "nope"}))
    assert await wait_for(lambda: len(db.query("SELECT 1 FROM events WHERE type='memory.write_result'")) == 4)
    pays = [r["payload"] for r in db.query("SELECT payload FROM events WHERE type='memory.write_result' ORDER BY rowid")]
    assert '"ok":true' in pays[0] and '"reason":"pii"' in pays[1] and '"ok":true' in pays[2] and "bad_op" in pays[3]
    assert db.one("SELECT status FROM facts WHERE fact='鍾意打麻雀'")["status"] == "active"
    assert db.one("SELECT photo_id FROM life_stories WHERE text='細個喺長洲住過。'")["photo_id"] == "ph_9"


async def test_non_final_and_empty_utterances_ignored(harness):
    h = harness
    wiring.install(svc_for(h))
    db, clock = h.kernel.db, h.kernel.clock
    await h.kernel.process(Event(type="user.utterance", source="agent", payload={"text": "我幾點食藥", "final": False}))
    await h.kernel.process(Event(type="user.utterance", source="agent", payload={"text": "  "}))
    await asyncio.sleep(0.2)
    assert db.query("SELECT * FROM repetition WHERE date=?", (clock.today().isoformat(),)) == []
