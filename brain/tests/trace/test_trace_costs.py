"""trace.costs (§15): one row per billable call; spend_today aggregates feed the caps (§11)."""
from __future__ import annotations

from ahbo.core.clock import FakeClock, set_clock
from ahbo.core.db import open_db
from ahbo.trace.costs import calls_for_incident, daily_totals, record_cost, spend_today


def setup_function(_: object) -> None:
    set_clock(FakeClock("2026-11-20T10:00:00+08:00"))


def test_record_cost_and_spend_today():
    from ahbo.core.clock import get_clock
    db = open_db(":memory:")
    record_cost(db, "deepseek", "deepseek-flash", {"prompt_tokens": 100, "completion_tokens": 20}, 0.0012,
                correlation_id="sess_1", purpose="voice_turn", clock=get_clock())
    record_cost(db, "anthropic", "claude-sonnet-5-5", {}, 0.05, correlation_id="deep_1", purpose="deep_task",
                clock=get_clock())
    record_cost(db, "twilio", None, {}, 0.0, correlation_id="inc_1", purpose="call", clock=get_clock())
    record_cost(db, "twilio", None, {}, 0.0, correlation_id="inc_1", purpose="safety_call", clock=get_clock())
    record_cost(db, "playwright", None, {}, 0.0, correlation_id="act_1", purpose="action", clock=get_clock())
    record_cost(db, "fake", None, {}, 0.0, purpose="voice_turn", clock=get_clock())   # fakes are free
    db.execute("INSERT INTO sessions(id, opened_at, closed_at, reason, mode) VALUES (?,?,?,?,?)",
               ("sess_1", "2026-11-20T09:00:00+08:00", None, "wake", "companion"))
    db.execute("INSERT INTO sessions(id, opened_at, closed_at, reason, mode) VALUES (?,?,?,?,?)",
               ("sess_old", "2026-11-19T09:00:00+08:00", "2026-11-19T09:10:00+08:00", "wake", "companion"))

    s = spend_today(db, get_clock())
    assert s["llm_usd"] == round(0.0012 + 0.05, 4)
    assert s["total_usd"] == round(0.0012 + 0.05, 4)      # the $0 rows don't change it
    assert s["deep_tasks"] == 1
    assert s["twilio_calls"] == 2
    assert s["playwright_actions"] == 1
    assert s["sessions"] == 1                              # only today's
    assert calls_for_incident(db, "inc_1") == 1            # only safety_call rows


def test_spend_today_is_per_hkt_day():
    from ahbo.core.clock import get_clock
    db = open_db(":memory:")
    record_cost(db, "deepseek", "m", {}, 0.01, clock=get_clock())
    late = FakeClock("2026-11-21T01:00:00+08:00")          # next HKT day
    record_cost(db, "deepseek", "m", {}, 0.01, clock=late)
    today = spend_today(db, FakeClock("2026-11-20T23:00:00+08:00"))
    tomorrow = spend_today(db, late)
    assert today["llm_usd"] == 0.01 and tomorrow["llm_usd"] == 0.01


def test_daily_totals():
    from ahbo.core.clock import get_clock
    db = open_db(":memory:")
    record_cost(db, "deepseek", "m", {}, 0.01, clock=get_clock())
    record_cost(db, "anthropic", "m", {}, 0.02, clock=get_clock())
    rows = daily_totals(db, days=7)
    assert {r["provider"]: r["usd"] for r in rows} == {"deepseek": 0.01, "anthropic": 0.02}
