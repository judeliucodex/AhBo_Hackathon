"""ops.caps (§11): per-HKT-day caps; safety Twilio calls exempt up to 3 per incident; one warning per day."""
from __future__ import annotations

from typing import Any

import pytest

from ahbo.core.clock import FakeClock, set_clock
from ahbo.core.db import open_db
from ahbo.ops.caps import CAP_NAMES, all_caps, check_cap, set_notifier
from ahbo.trace.costs import record_cost

CAPS = {"llm_usd": 3.0, "deep_tasks": 3, "twilio_calls": 2, "playwright_actions": 2,
        "sessions_per_day": 5, "session_minutes": 10}


class Cfg:
    def __init__(self, caps: dict[str, Any]):
        self.caps = caps


@pytest.fixture
def env():
    set_clock(FakeClock("2026-11-20T10:00:00+08:00"))
    db = open_db(":memory:")
    yield db
    set_notifier(None)


def test_check_cap_counts_llm_usd(env):
    clock = FakeClock("2026-11-20T10:00:00+08:00")
    ok, used, cap = check_cap(env, clock, CAPS, "llm_usd")
    assert (ok, used, cap) == (True, 0.0, 3.0)
    record_cost(env, "deepseek", "m", {}, 2.0, clock=clock)
    ok, used, _ = check_cap(env, clock, CAPS, "llm_usd")
    assert ok and used == 2.0                       # dollars: used < cap allows one more call
    record_cost(env, "anthropic", "m", {}, 1.5, clock=clock)
    ok, used, _ = check_cap(env, clock, CAPS, "llm_usd")
    assert not ok and used == 3.5


def test_check_cap_counts_units_not_dollars(env):
    clock = FakeClock("2026-11-20T10:00:00+08:00")
    for i in range(3):
        record_cost(env, "fake", None, {}, 0.0, correlation_id=f"deep_{i}", purpose="deep_task", clock=clock)
    ok, used, cap = check_cap(env, clock, CAPS, "deep_tasks")
    assert not ok and used == 3 and cap == 3        # used + 1 <= cap fails


def test_check_cap_accepts_config_object_or_dict(env):
    clock = FakeClock("2026-11-20T10:00:00+08:00")
    assert check_cap(env, clock, Cfg(CAPS), "twilio_calls")[2] == 2
    assert check_cap(env, clock, CAPS, "twilio_calls")[2] == 2


def test_twilio_safety_calls_exempt_up_to_3_per_incident(env):
    clock = FakeClock("2026-11-20T10:00:00+08:00")
    for _ in range(3):                      # the checks before the first 3 safety calls: exempt (§11)
        ok, _, _ = check_cap(env, clock, CAPS, "twilio_calls", safety=True, incident_id="inc_1")
        assert ok
        record_cost(env, "twilio", None, {}, 0.0, correlation_id="inc_1", purpose="safety_call", clock=clock)
    ok, used, _ = check_cap(env, clock, CAPS, "twilio_calls", safety=True, incident_id="inc_1")
    assert not ok and used == 3              # the 4th safety call for the same incident counts
    record_cost(env, "twilio", None, {}, 0.0, correlation_id="inc_1", purpose="safety_call", clock=clock)
    ok, used, _ = check_cap(env, clock, CAPS, "twilio_calls", safety=True, incident_id="inc_1")
    assert not ok and used == 4
    ok, _, _ = check_cap(env, clock, CAPS, "twilio_calls", safety=True, incident_id="inc_2")
    assert ok                                # a different incident gets its own 3


def test_twilio_regular_calls_hit_the_cap(env):
    clock = FakeClock("2026-11-20T10:00:00+08:00")
    for _ in range(2):
        record_cost(env, "twilio", None, {}, 0.0, correlation_id="inc_1", purpose="call", clock=clock)
    ok, used, _ = check_cap(env, clock, CAPS, "twilio_calls")
    assert not ok and used == 2


def test_session_minutes(env):
    clock = FakeClock("2026-11-20T10:00:00+08:00")
    env.execute("INSERT INTO sessions(id, opened_at, closed_at, reason, mode) VALUES (?,?,?,?,?)",
                ("sess_1", "2026-11-20T09:00:00+08:00", "2026-11-20T09:09:30+08:00", "wake", "companion"))
    ok, used, cap = check_cap(env, clock, CAPS, "session_minutes", session_id="sess_1")
    assert ok and used == 9.5 and cap == 10.0
    env.execute("UPDATE sessions SET closed_at='2026-11-20T09:20:00+08:00' WHERE id='sess_1'")
    ok, used, _ = check_cap(env, clock, CAPS, "session_minutes", session_id="sess_1")
    assert not ok and used == 20.0
    # an open session is measured against the (virtual) clock
    env.execute("UPDATE sessions SET closed_at=NULL, opened_at='2026-11-20T09:55:00+08:00' WHERE id='sess_1'")
    ok, used, _ = check_cap(env, clock, CAPS, "session_minutes", session_id="sess_1")
    assert ok and used == 5.0


def test_sessions_per_day_counts_only_today(env):
    clock = FakeClock("2026-11-20T10:00:00+08:00")
    for i in range(5):
        day = "2026-11-20" if i < 4 else "2026-11-19"
        env.execute("INSERT INTO sessions(id, opened_at, reason, mode) VALUES (?,?,?,?)",
                    (f"sess_{i}", f"{day}T09:00:00+08:00", "wake", "companion"))
    ok, used, cap = check_cap(env, clock, CAPS, "sessions_per_day")
    assert ok and used == 4 and cap == 5.0            # yesterday's session doesn't count
    env.execute("INSERT INTO sessions(id, opened_at, reason, mode) VALUES (?,?,?,?)",
                ("sess_9", "2026-11-20T11:00:00+08:00", "wake", "companion"))
    ok, used, _ = check_cap(env, clock, CAPS, "sessions_per_day")
    assert not ok and used == 5


def test_unknown_cap_raises(env):
    with pytest.raises(ValueError, match="unknown cap"):
        check_cap(env, FakeClock("2026-11-20T10:00:00+08:00"), CAPS, "nonsense")


def test_missing_or_zero_cap_disables_nothing(env):
    clock = FakeClock("2026-11-20T10:00:00+08:00")
    for _ in range(10):
        record_cost(env, "twilio", None, {}, 0.0, purpose="call", clock=clock)
    ok, used, cap = check_cap(env, clock, {"llm_usd": 3.0}, "twilio_calls")   # not configured → no cap
    assert ok and cap == 0.0 and used == 10
    ok, _, cap = check_cap(env, clock, {"playwright_actions": 0}, "playwright_actions")
    assert ok and cap == 0.0


def test_warning_recorded_once_per_day_and_notifier_called(env):
    clock = FakeClock("2026-11-20T10:00:00+08:00")
    seen: list[dict] = []
    set_notifier(seen.append)
    record_cost(env, "deepseek", "m", {}, 5.0, clock=clock)
    check_cap(env, clock, CAPS, "llm_usd")
    check_cap(env, clock, CAPS, "llm_usd")                    # same day: only one warning
    rows = env.query("SELECT * FROM quality WHERE kind='cap_exceeded'")
    assert len(rows) == 1 and len(seen) == 1
    assert seen[0]["name"] == "llm_usd" and seen[0]["date"] == "2026-11-20"
    assert env.kv_get("cap_warned:2026-11-20:llm_usd") is True
    # the next HKT day warns again (its own spend)
    tomorrow = FakeClock("2026-11-21T10:00:00+08:00")
    record_cost(env, "deepseek", "m", {}, 5.0, clock=tomorrow)
    check_cap(env, tomorrow, CAPS, "llm_usd")
    assert len(env.query("SELECT * FROM quality WHERE kind='cap_exceeded'")) == 2
    assert len(seen) == 2


def test_notifier_failure_does_not_break_check(env):
    clock = FakeClock("2026-11-20T10:00:00+08:00")

    def boom(detail):
        raise RuntimeError("kernel down")

    set_notifier(boom)
    record_cost(env, "deepseek", "m", {}, 5.0, clock=clock)
    ok, used, cap = check_cap(env, clock, CAPS, "llm_usd")
    assert not ok and used == 5.0 and cap == 3.0


def test_all_caps_for_console(env):
    clock = FakeClock("2026-11-20T10:00:00+08:00")
    record_cost(env, "deepseek", "m", {}, 1.0, clock=clock)
    out = all_caps(env, clock, CAPS)
    assert set(CAP_NAMES) <= set(out)
    assert out["llm_usd"] == {"used": 1.0, "cap": 3.0, "ok": True}
    assert out["deep_tasks"]["used"] == 0
    assert out["session_minutes"]["ok"] is True
    record_cost(env, "deepseek", "m", {}, 2.5, clock=clock)
    assert all_caps(env, clock, CAPS)["llm_usd"]["ok"] is False
