"""QA23 check 3: persistence + effect — a new check-in time actually moves when checkin.due fires (real
schedule ticks through the Kernel); a medicine change re-renders the reminder clip and pushes
device.config_update with the new reminder_mirror; quiet hours reach the device config and survive a restart.
"""
from __future__ import annotations

import asyncio

import pytest
from qa_helpers import bot_tasks, commands_of, confirm, events_of, make_svc_fixture, say, stop_all, tap

CHAT = "8925008896"
HKT = None


@pytest.fixture
async def svc(tmp_path):
    made: list = []
    s = await make_svc_fixture(tmp_path, made)
    s._test_tmp_path = tmp_path
    yield s
    await stop_all(made)


def _texts(replies):
    return [r["text"] for r in replies]


def _btn(replies, prefix):
    for t, d in {b["text"]: b["callback_data"] for r in replies for row in r["buttons"] for b in row}.items():
        if t.startswith(prefix):
            return d
    raise AssertionError(f"no button {prefix!r}")


async def _tick_hour(svc, hh: int):
    """Advance the fake clock through hour `hh` in 60 s steps, running the real Scheduler.tick each step
    (the same code the running server's loop uses)."""
    clock = svc.kernel.clock
    sched = svc.kernel.scheduler
    day = clock.now().date()
    seen = len(events_of(svc, "checkin.due"))
    for i in range(66):                      # a bit past the hour end
        clock.set_time(f"{hh:02d}:00", on=day)
        clock.advance(i * 60)
        day = clock.now().date()
        await sched.tick()
        while not svc.kernel.queue.empty():
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        due = [e for e in events_of(svc, "checkin.due")]
        if len(due) > seen:
            seen = len(due)
            yield (clock.now(), due[-1])     # the clock time at which it crossed due
            return


async def test_new_checkin_time_moves_when_checkin_due_fires(svc):
    """Control: with no change the morning check-in fires at the seeded 08:30. Experiment: move it to 09:10
    BEFORE it has fired today — nothing fires at 08:30 any more, it fires at 09:10 instead."""
    from ahbo.kernel.scheduler import daily_items  # noqa: F401  (sanity that the module is the real one)
    # control svc: unchanged seed → fires at 08:30
    made: list = []
    ctrl = await make_svc_fixture(svc._test_tmp_path, made)   # same tmp dir, separate in-memory db
    ctrl.kernel.clock.set_time("08:00", on=ctrl.kernel.clock.now().date())
    fired_ctrl = None
    for i in range(35):
        ctrl.kernel.clock.advance(60)
        await ctrl.kernel.scheduler.tick()
        while not ctrl.kernel.queue.empty():
            await asyncio.sleep(0.01)
        if [e for e in events_of(ctrl, "checkin.due") if e["payload"]["kind"] == "morning"]:
            fired_ctrl = ctrl.kernel.clock.now()
            break
    await stop_all(made)
    assert fired_ctrl is not None and fired_ctrl.strftime("%H:%M") == "08:30", \
        f"control run: morning check-in did not fire at 08:30 (fired at {fired_ctrl})"

    # experiment svc: move morning to 09:10 BEFORE it fires today
    svc.kernel.clock.set_time("08:00", on=svc.kernel.clock.now().date())
    r = await tap(svc, "m:f:ck_morning")
    r = await say(svc, "09:10")
    r = await confirm(svc, r)
    assert any("09:10" in t for t in _texts(r))

    fired_at = None
    for i in range(75):
        svc.kernel.clock.advance(60)          # 08:00 → 09:15, 60 s steps (≤ the 120 s jump guard)
        await svc.kernel.scheduler.tick()
        while not svc.kernel.queue.empty():
            await asyncio.sleep(0.01)
        due = [e for e in events_of(svc, "checkin.due") if e["payload"]["kind"] == "morning"]
        if due:
            fired_at = svc.kernel.clock.now()
            break
    assert fired_at is not None, "morning check-in never fired after the move"
    assert fired_at.strftime("%H:%M") == "09:10", f"fired at {fired_at.strftime('%H:%M')}, not the new 09:10"
    # and no dose/other check-in burst happened at the old 08:30
    kinds = [(svc.kernel.clock.now(), e) for e in events_of(svc, "checkin.due")]
    assert len(kinds) == 1, kinds


async def test_checkin_change_survives_a_restart_and_still_fires(tmp_path):
    from ahbo.core.events import Event
    made: list = []
    db_path = str(tmp_path / "ahbo.sqlite")
    s = await make_svc_fixture(tmp_path, made, db_path=db_path)
    r = await tap(s, "m:f:ck_morning")
    r = await say(s, "08:45")
    await confirm(s, r)
    await stop_all(made)

    made2: list = []
    s2 = await make_svc_fixture(tmp_path, made2, db_path=db_path)
    try:
        assert s2.db.kv_get("settings_overlay")["morning"] == "08:45", "overlay lost across restart"
        clock = s2.kernel.clock
        day = clock.now().date()
        fired_at = None
        for i in range(66):
            clock.set_time("08:00", on=day)
            clock.advance(i * 60)
            day = clock.now().date()
            await s2.kernel.scheduler.tick()
            while not s2.kernel.queue.empty():
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
            due = [e for e in events_of(s2, "checkin.due") if e["payload"]["kind"] == "morning"]
            if due:
                fired_at = clock.now()
                break
        assert fired_at is not None, "check-in did not fire after restart"
        assert fired_at.strftime("%H:%M") == "08:45", fired_at
    finally:
        await stop_all(made2)


async def test_med_change_rerenders_clip_and_pushes_reminder_mirror(svc):
    import json
    from pathlib import Path

    # baseline mirror
    r = await say(svc, "/meds")
    before_cmds = commands_of(svc, "device.config_update")
    assert not before_cmds, "a read-only view pushed config"

    # add a medicine + a slot that uses it, through the bot
    r = await tap(svc, "m:f:med_add")
    r = await say(svc, "胃藥")
    r = await say(svc, "-")
    r = await say(svc, "-")
    r = await tap(svc, _btn(r, "白／黃"))
    await confirm(svc, r)
    await bot_tasks(svc)                       # the re-render runs in a background task
    mid = svc.db.one("SELECT id FROM med_catalogue WHERE yue='胃藥'")["id"]

    cmds = commands_of(svc, "device.config_update")
    mirror_cmds = [c for c in cmds if "reminder_mirror" in c["payload"]]
    assert mirror_cmds, f"no device.config_update with reminder_mirror after a medicine change: {len(cmds)}"
    mirror = mirror_cmds[-1]["payload"]["reminder_mirror"]
    print("MIRROR:", mirror)
    assert "胃藥" not in str(mirror), "mirror updated before any slot uses the new medicine"

    r = await tap(svc, "m:f:slot_add")
    r = await say(svc, "16:30")
    r = await tap(svc, f"m:i:{mid}")
    r = await tap(svc, "m:i:_done")
    r = await confirm(svc, r)
    await bot_tasks(svc)

    cmds = [c for c in commands_of(svc, "device.config_update") if "reminder_mirror" in c["payload"]]
    assert cmds, "no reminder_mirror push after the slot change"
    mirror = cmds[-1]["payload"]["reminder_mirror"]
    print("MIRROR AFTER SLOT:", mirror)
    s = str(mirror)
    assert "16:30" in s and "胃藥" in s, f"mirror does not carry the new slot: {s[:300]}"
    assert cmds[-1]["payload"].get("fixed_cache_version"), "no fixed_cache_version pushed with the mirror"

    # the clips were really re-rendered into the (container-local) data dir
    man_path = Path(svc.settings.media_dir).parent / "fixed" / "manifest.json"
    if not man_path.exists():
        man_path = Path(svc.settings.data_dir) / "media" / "fixed" / "manifest.json"
    assert man_path.exists(), f"no rendered manifest at {man_path}"
    man = json.loads(man_path.read_text())
    r_clips = {k: v for k, v in (man.get("clips") or {}).items() if k.startswith("R_")}
    print("R CLIPS:", sorted(r_clips))
    assert any("1630" in k for k in r_clips), f"no R clip for the new 16:30 slot: {sorted(r_clips)}"


async def test_quiet_hours_reach_device_config_and_kernel(svc):
    from ahbo.core.events import Event
    from ahbo.kernel import common
    from ahbo.core.jsonutil import loads

    r = await say(svc, "/quiet 21:00 08:00")
    r = await confirm(svc, r)
    cmds = [c for c in commands_of(svc, "device.config_update") if "quiet_hours" in c["payload"]]
    assert cmds, "quiet-hours change produced no device.config_update"
    q = cmds[-1]["payload"]["quiet_hours"]
    assert q["start"] == "21:00" and q["end"] == "08:00", q
    assert loads(svc.db.one("SELECT quiet_hours FROM resident")["quiet_hours"], {}) == q, \
        "resident.quiet_hours not mirrored for the Device Hub welcome config"
    # the Kernel's arbiter/check-in logic sees it too (virtual 15:00 → now quiet)
    # overnight span: quiet at 07:30 (inside 21:00→08:00)
    svc.kernel.clock.set_time("07:30", on=svc.kernel.clock.now().date())
    assert common.quiet_hours(svc.kernel.ctx()) is True, "07:30 not quiet inside 21:00–08:00"
    # and a check-in during quiet hours is skipped
    n = len(events_of(svc, "checkin.skipped"))
    await svc.kernel.process(Event(type="checkin.due", source="scheduler", payload={"kind": "afternoon"}))
    sk = events_of(svc, "checkin.skipped")
    assert len(sk) == n + 1 and sk[-1]["payload"]["reason"] == "quiet_hours", sk[-1]["payload"]
    # daytime 15:00 is not quiet — the same overlay does not silence everything
    svc.kernel.clock.set_time("15:00", on=svc.kernel.clock.now().date())
    assert common.quiet_hours(svc.kernel.ctx()) is False
