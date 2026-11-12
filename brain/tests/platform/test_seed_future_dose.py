"""QA 25 D1: a demo reset before 08:00 must not mark the 08:00 dose as taken (INV-4)."""
from ahbo.ops.seed import load_demo


def _taken(h):
    return [r["slot_id"] for r in h.kernel.db.query("SELECT slot_id FROM dose_events WHERE state='taken_self_reported'")]


def test_seed_before_0800_leaves_dose_open(harness):
    harness.kernel.clock.set_time("07:00")
    load_demo(harness.kernel.db, harness.kernel.clock, harness.kernel.settings)
    assert "s_0800" not in _taken(harness)


def test_seed_after_0805_marks_dose_taken(harness):
    harness.kernel.clock.set_time("09:00")
    load_demo(harness.kernel.db, harness.kernel.clock, harness.kernel.settings)
    assert "s_0800" in _taken(harness)
