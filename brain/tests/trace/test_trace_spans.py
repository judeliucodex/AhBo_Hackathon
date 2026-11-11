"""trace.spans (§15): recording, sync/async context manager, decorator, TurnTimer, latency stats."""
from __future__ import annotations

import asyncio

import pytest

from ahbo.core.clock import FakeClock, get_clock, set_clock
from ahbo.core.db import open_db
from ahbo.trace import spans


@pytest.fixture
def db():
    set_clock(FakeClock("2026-11-20T10:00:00+08:00"))
    return open_db(":memory:")


def test_record_span_stores_row(db):
    sid = spans.record_span(db, "tool.get_med_status", correlation_id="sess_1", duration_ms=12.34,
                            attrs={"turn_id": "t1"})
    row = db.one("SELECT * FROM spans WHERE id=?", (sid,))
    assert row["name"] == "tool.get_med_status" and row["correlation_id"] == "sess_1"
    assert row["duration_ms"] == 12.3 and row["status"] == "ok"
    assert row["start_ts"].startswith("2026-11-20T10:00")      # virtual clock, HKT
    assert row["end_ts"] == row["start_ts"]
    assert row["attrs"] == '{"turn_id":"t1"}'


def test_record_span_without_db_is_none(db):
    spans.set_default_db(None)
    assert spans.record_span(None, "x") is None
    assert spans.get_default_db() is None


def test_span_context_manager_sync_marks_error(db):
    with pytest.raises(RuntimeError), spans.span(db, "llm.turn", correlation_id="sess_2", provider="fake") as s:
        s.attrs["model"] = "fake-flash"
        raise RuntimeError("boom")
    row = db.one("SELECT * FROM spans WHERE correlation_id='sess_2'")
    assert row["status"] == "error"
    assert row["attrs"] == '{"provider":"fake","model":"fake-flash","error":"RuntimeError"}'


def test_span_context_manager_async(db):
    async def go():
        async with spans.span(db, "telegram.send", correlation_id="inc_1"):
            await asyncio.sleep(0.01)
    asyncio.run(go())
    row = db.one("SELECT * FROM spans WHERE correlation_id='inc_1'")
    assert row["name"] == "telegram.send" and row["status"] == "ok" and row["duration_ms"] >= 10


def test_traced_decorator_sync_and_async(db):
    spans.set_default_db(db)

    @spans.traced("tool.recall")
    def sync_fn(correlation_id: str | None = None) -> str:
        return "ok"

    @spans.traced("deep.step")
    async def async_fn(correlation_id: str | None = None) -> str:
        return "done"

    assert sync_fn(correlation_id="sess_9") == "ok"
    assert asyncio.run(async_fn(correlation_id="sess_9")) == "done"
    names = sorted(r["name"] for r in db.query("SELECT name FROM spans WHERE correlation_id='sess_9'"))
    assert names == ["deep.step", "tool.recall"]
    spans.set_default_db(None)


def test_turn_timer_latency_log(db):
    db.execute("INSERT INTO turns(id, session_id, ts, role, text) VALUES (?,?,?,?,?)",
               ("turn_1", "sess_5", get_clock().iso(), "user", "你好"))
    t = spans.TurnTimer("turn_1", session_id="sess_5")
    t.mark("eos", at=0.0)
    t.mark("stt_final", at=0.25)
    t.mark("llm_ttft", at=1.0)
    t.mark("tts_first_audio", at=1.6)
    lat = t.finish(db)
    assert lat["total_ms"] == 1600
    assert lat["stt_final_ms"] == 250 and lat["llm_ttft_ms"] == 1000 and lat["tts_first_audio_ms"] == 1600
    assert lat["eos_to_stt_final_ms"] == 250
    assert lat["stt_final_to_llm_ttft_ms"] == 750
    assert lat["llm_ttft_to_tts_first_audio_ms"] == 600
    row = db.one("SELECT attrs, duration_ms FROM spans WHERE name='turn.latency'")
    assert row["duration_ms"] == 1600.0
    t2 = spans.TurnTimer("turn_1", session_id="sess_5")
    t2.mark("eos", at=0.0)
    t2.mark("stt_final", at=0.1)
    t2.finish(db)
    assert db.one("SELECT latency FROM turns WHERE id='turn_1'")["latency"] != "{}"


def test_turn_timer_rejects_unknown_stage():
    with pytest.raises(ValueError, match="stage"):
        spans.TurnTimer("t").mark("not_a_stage")


def test_latency_stats_p50_p95(db):
    for i in range(1, 21):                                             # 20 turns, 1 s … 20 s
        total = float(i * 1000)
        spans.record_span(db, "turn.latency", correlation_id=f"s{i}", duration_ms=total,
                          attrs={"turn_id": f"t{i}", "total_ms": total, "stt_final_ms": total / 2,
                                 "llm_ttft_ms": total * 0.75, "llm_ttft_to_tts_first_audio_ms": total * 0.25})
    st = spans.latency_stats(db, n=50)
    assert st["count"] == 20
    assert st["total_ms"]["p50"] == 10500.0        # interpolated between the 10th and 11th values
    assert st["total_ms"]["p95"] == 19050.0
    assert st["llm_ttft_to_tts_first_audio_ms"]["p50"] == 2625.0
    # only the last n turns are counted
    assert spans.latency_stats(db, n=5)["count"] == 5


def test_percentile():
    assert spans.percentile([], 50) is None
    assert spans.percentile([5.0], 95) == 5.0
    assert spans.percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
