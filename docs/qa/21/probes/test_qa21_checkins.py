"""QA21 check 6: check-ins — times, skip/defer rules, quiet hours, caregiver overlay, no-reply close."""
from __future__ import annotations
import asyncio, sys
sys.path.insert(0, "/repo/brain/tests")
import pytest
from conftest import harness  # noqa: F401
from qa import watch, of, faces, clips, until, ev, inc
from ahbo.core.jsonutil import loads

def skips(h):
    return [(loads(r["payload"]).get("kind"), loads(r["payload"]).get("reason"))
            for r in h.kernel.db.query("SELECT payload FROM events WHERE type='checkin.skipped'")]

async def open_session(h):
    await ev(h, "device.wake", "device", trigger="touch")
    assert h.kernel.state.session is not None

async def close_session(h):
    from ahbo.core.events import Event
    sess = h.kernel.state.session
    if sess:
        await ev(h, "session.closed", "system", session_id=sess.id, why="idle")

def injects(h):
    return [c["payload"] for c in h.cmds if c["type"] == "agent.inject"]


async def test_morning_checkin_fires_at_configured_time(harness):
    h = harness; watch(h)
    h.kernel.clock.set_time("08:31")
    await ev(h, "checkin.due", "scheduler", kind="morning")
    assert any(u.get("mood") == "curious" for u in faces(h, "mood"))
    assert await until(lambda: any(i["kind"] == "問候" for i in injects(h)), 4)
    sess = h.kernel.state.session
    assert sess is not None and sess.reason == "checkin_morning"
    assert "早晨" in [i for i in injects(h) if i["kind"] == "問候"][-1]["content"]


async def test_checkin_never_in_quiet_hours(harness):
    h = harness
    h.kernel.clock.set_time("23:00")
    await ev(h, "checkin.due", "scheduler", kind="evening")
    await asyncio.sleep(0.2)
    assert skips(h) == [("evening", "quiet_hours")]
    assert h.kernel.state.session is None


async def test_checkin_skipped_within_20min_of_session(harness):
    h = harness; watch(h)
    h.kernel.clock.set_time("10:00")
    await open_session(h)
    await close_session(h)                                  # just closed → within 20 min
    await ev(h, "checkin.due", "scheduler", kind="afternoon")
    await asyncio.sleep(0.2)
    assert ("afternoon", "session_recent") in skips(h)
    h.kernel.clock.advance(21 * 60)                         # 21 min later → allowed
    await ev(h, "checkin.due", "scheduler", kind="afternoon")
    assert await until(lambda: any(i["kind"] == "問候" for i in injects(h)) if h.cmds else False, 4)


async def test_checkin_deferred_while_session_open_then_dropped_at_30min(harness):
    h = harness; watch(h)
    h.kernel.clock.set_time("15:01")
    await open_session(h)
    await ev(h, "checkin.due", "scheduler", kind="afternoon")
    await asyncio.sleep(0.2)
    assert not injects(h) and skips(h) == [], "deferred while a session is open"
    h.kernel.clock.advance(10 * 60)
    await ev(h, "checkin.due", "scheduler", kind="afternoon")   # still open, still deferred
    assert not injects(h)
    h.kernel.clock.advance(21 * 60)                             # > 30 min deferred → dropped
    await ev(h, "checkin.due", "scheduler", kind="afternoon")
    await asyncio.sleep(0.2)
    assert ("afternoon", "session_open_30min") in skips(h)


async def test_overlay_changes_checkin_time(harness):
    from ahbo.kernel.scheduler import Scheduler, due_events
    from ahbo.settings_store import set_values, checkin_times
    h = harness
    set_values(h.kernel.db, {"morning": "09:15"})
    assert checkin_times(h.kernel.db, h.kernel.config)["morning"] == "09:15"
    sched = Scheduler(h.kernel)
    h.kernel.clock.set_time("09:14")
    evs = await sched.tick()
    assert not [e for e in evs if e.type == "checkin.due"]
    h.kernel.clock.advance(120)                                  # crosses 09:15 naturally
    evs = await sched.tick()
    dues = [e for e in evs if e.type == "checkin.due"]
    assert dues and dues[0].payload["kind"] == "morning"
    watch(h)
    assert await until(lambda: any(i["kind"] == "問候" for i in injects(h)), 4), "morning fires at 09:15"


async def test_no_reply_closes_the_checkin_session(harness):
    h = harness; watch(h)
    h.kernel.clock.set_time("20:31")
    await ev(h, "checkin.due", "scheduler", kind="evening")
    assert await until(lambda: any(i["kind"] == "問候" for i in injects(h)), 4)
    sid = h.kernel.state.session.id
    await asyncio.sleep(1.6)                                     # 20 s × 0.05 + margin
    assert h.kernel.state.session is None or h.kernel.state.session.id != sid, \
        "no reply within 20 s → the session closes"
    closes = [c for c in h.cmds if c["type"] == "session.close" or
              (c["type"] == "agent.session" and c["payload"].get("op") == "close")]
    print("NO-REPLY close cmds:", [c["type"] for c in closes] or "(via session reducer)")
