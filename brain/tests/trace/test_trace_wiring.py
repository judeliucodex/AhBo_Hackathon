"""trace.wiring: the default DB for spans and the cap notifier (an informational Kernel event, INV-6/INV-8)."""
from __future__ import annotations

from types import SimpleNamespace

from ahbo.ops.caps import check_cap, set_notifier
from ahbo.trace import spans
from ahbo.trace.costs import record_cost


def test_install_sets_default_db_and_cap_notifier(harness):
    from ahbo.trace.wiring import install

    svc = SimpleNamespace(kernel=harness.kernel, db=harness.kernel.db)
    install(svc)
    try:
        assert spans.get_default_db() is svc.db
        # a cap warning now surfaces as a `system.cap_exceeded` event in the Kernel trace
        record_cost(svc.db, "deepseek", "m", {"tokens": 1}, 9.9, clock=harness.kernel.clock)
        ok, used, _cap = check_cap(svc.db, harness.kernel.clock, {"llm_usd": 3.0}, "llm_usd")
        assert not ok and used == 9.9
        rows = svc.db.query("SELECT type, payload FROM events WHERE type='system.cap_exceeded'")
        assert len(rows) == 1
        assert "llm_usd" in rows[0]["payload"]
    finally:
        set_notifier(None)
        spans.set_default_db(None)
