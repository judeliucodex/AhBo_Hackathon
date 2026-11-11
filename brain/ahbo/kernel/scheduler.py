"""Scheduler (§4.2, §13.3): a 15 s tick on the virtual clock.

Every tick submits `schedule.tick {now}` (dose re-prompts/missed, deferred check-ins and the arbiter queue run on it),
then the once-per-day events whose time the virtual clock has just crossed:
`dose.due` (med_schedule), `checkin.due` (checkins.yaml + the caregiver overlay, `ahbo.settings_store`), `digest.due`, `plan.due` (07:00) and `event.leave_soon`
(10 min before a calendar `plan` item, e.g. 「9:30 出門」). It also raises `sensor.lost` once per beacon after 90 s
without a heartbeat (console warning only, §9.1).

A due time fires only when a tick crosses it naturally (previous tick < due ≤ now, and the clock moved ≤ 120 s since
the previous tick). When the operator jumps the virtual clock, scheduled items in the jumped-over span are skipped
(use the console's dose_now / checkin_now / plan_now), so a jump never fires a burst of stale reminders.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta
from typing import Any

from ahbo.core.events import Event

log = logging.getLogger("ahbo.kernel.scheduler")

TICK_S = 15.0
JUMP_S = 120.0
BEACON_LOST_S = 90.0
LEAVE_SOON_MIN = 10
PLAN_AT = "07:00"
KV = "scheduler"


def _at(day: date, hhmm: str, tz: Any) -> datetime:
    h, m = (int(x) for x in hhmm.split(":")[:2])
    return datetime.combine(day, time(h, m), tz)


def daily_items(db: Any, config: Any, day: date, tz: Any) -> list[tuple[str, datetime, str, dict[str, Any]]]:
    """(key, when, event type, payload) for everything scheduled on `day`."""
    d = day.isoformat()
    out: list[tuple[str, datetime, str, dict[str, Any]]] = []
    for r in db.query("SELECT slot_id, time FROM med_schedule WHERE active=1"):
        out.append((f"dose:{r['slot_id']}", _at(day, r["time"], tz), "dose.due", {"slot_id": r["slot_id"], "date": d}))
    from ahbo.settings_store import checkin_times
    ck = checkin_times(db, config)          # caregiver overlay (Telegram) on top of checkins.yaml
    for kind in ("morning", "afternoon", "evening"):
        if ck.get(kind):
            out.append((f"checkin:{kind}", _at(day, str(ck[kind]), tz), "checkin.due", {"kind": kind, "date": d}))
    # Owner decision 12 Nov: no 07:00 day-plan precompute and no 20:00 family digest (digest.due / plan.due removed).
    for r in db.query("SELECT id, datetime FROM calendar_events WHERE kind='plan' AND substr(datetime,1,10)=? "
                      "AND status != 'cancelled'", (d,)):
        try:
            when = datetime.fromisoformat(r["datetime"]) - timedelta(minutes=LEAVE_SOON_MIN)
        except ValueError:
            continue
        out.append((f"leave:{r['id']}", when, "event.leave_soon", {"event_id": r["id"], "date": d}))
    return out


def due_events(db: Any, config: Any, prev: datetime | None, now: datetime, fired: set[str]) -> list[Event]:
    """Events for items crossed in (prev, now]; nothing on the first tick or after a clock jump."""
    if prev is None or now <= prev or (now - prev).total_seconds() > JUMP_S:
        return []
    days = {prev.date(), now.date()}
    evs = []
    for day in sorted(days):
        for key, when, typ, payload in daily_items(db, config, day, now.tzinfo):
            k = f"{day.isoformat()}:{key}"
            if prev < when <= now and k not in fired:
                fired.add(k)
                evs.append(Event(type=typ, source="scheduler", payload=payload))
    return evs


class Scheduler:
    def __init__(self, kernel: Any):
        self.kernel = kernel
        self.prev: datetime | None = None
        self.task: asyncio.Task | None = None
        saved = kernel.db.kv_get(KV, {}) or {}
        self.fired: set[str] = set(saved.get("fired") or [])

    def start(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.ensure_future(self._run())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
            self.task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(TICK_S * self.kernel.time_scale)
            try:
                await self.tick()
            except Exception:
                log.exception("scheduler tick failed")

    async def tick(self) -> list[Event]:
        k = self.kernel
        now = k.clock.now()
        today = now.date().isoformat()
        self.fired = {f for f in self.fired if f[:10] >= (now.date() - timedelta(days=1)).isoformat()}
        evs = [Event(type="schedule.tick", source="scheduler", payload={"now": now.isoformat()})]
        evs += due_events(k.db, k.config, self.prev, now, self.fired)
        self.prev = now
        k.db.kv_set(KV, {"date": today, "fired": sorted(self.fired)})
        evs += self._beacons()
        for ev in evs:
            await k.submit(ev)
        return evs

    def _beacons(self) -> list[Event]:
        k = self.kernel
        out = []
        for bid, b in list((k.state.extra.get("beacons") or {}).items()):
            seen = b.get("seen_mono")
            if seen is None or b.get("lost"):
                continue
            if k.monotonic() - seen > BEACON_LOST_S * k.time_scale:
                b["lost"] = True
                out.append(Event(type="sensor.lost", source="system", payload={"beacon_id": bid,
                                                                             "last_seen": b.get("at")}))
        return out
