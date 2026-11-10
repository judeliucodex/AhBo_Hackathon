"""The virtual clock (§13.3): real HKT plus an offset set by the console. The ONLY time source for the Kernel
and tools. Kernel timers measure durations with the event loop's monotonic clock (offsets never stretch time).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

HKT = ZoneInfo("Asia/Hong_Kong")


class Clock:
    def __init__(self, offset_s: float = 0.0):
        self.offset_s = float(offset_s)

    def real_now(self) -> datetime:
        return datetime.now(HKT)

    def now(self) -> datetime:
        return self.real_now() + timedelta(seconds=self.offset_s)

    def today(self) -> date:
        return self.now().date()

    def iso(self) -> str:
        return self.now().isoformat(timespec="milliseconds")

    def set_offset(self, offset_s: float) -> None:
        self.offset_s = float(offset_s)

    def set_time(self, hhmm: str, on: date | None = None) -> None:
        """Make the virtual clock read `hhmm` today (or on `on`), keeping seconds at 0."""
        h, m = (int(x) for x in hhmm.split(":"))
        target = datetime.combine(on or self.real_now().date(), time(h, m), HKT)
        self.offset_s = (target - self.real_now()).total_seconds()

    def at_today(self, hhmm: str) -> datetime:
        h, m = (int(x) for x in hhmm.split(":"))
        return datetime.combine(self.today(), time(h, m), HKT)


class FakeClock(Clock):
    """Test clock: frozen at a given HKT datetime, advanced manually."""

    def __init__(self, start: datetime | str = "2026-11-20T10:00:00+08:00"):
        super().__init__(0.0)
        if isinstance(start, str):
            start = datetime.fromisoformat(start)
        self._now = start.astimezone(HKT)

    def real_now(self) -> datetime:
        return self._now

    def now(self) -> datetime:
        return self._now + timedelta(seconds=self.offset_s)

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)

    def set_time(self, hhmm: str, on: date | None = None) -> None:
        h, m = (int(x) for x in hhmm.split(":"))
        self._now = datetime.combine(on or self._now.date(), time(h, m), HKT)
        self.offset_s = 0.0


_clock = Clock()


def get_clock() -> Clock:
    return _clock


def set_clock(c: Clock) -> None:
    global _clock
    _clock = c
