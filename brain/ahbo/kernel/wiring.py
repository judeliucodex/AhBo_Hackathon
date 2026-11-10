"""Kernel wiring (AGENT_BRIEF convention): reducers (+ their loaded hooks), the scheduler, restart recovery.

`install(svc)` is idempotent with respect to reducers: harnesses that already called `register_all` (the eval
system) keep their registration. Restart recovery runs from `Kernel.load()` (the platform calls it on start):
each flow's loaded hook rebuilds its slice from SQLite (incident, doses, open actions; sessions are closed and the
face mirror loses a stale `listening` mood).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .reducers import register_all
from .scheduler import Scheduler

log = logging.getLogger("ahbo.kernel")

router = None


def install(svc: Any) -> None:
    k = svc.kernel
    if not k.reducers:
        names = register_all(k)
        if hasattr(svc, "reducers"):
            svc.reducers = names
    sched = Scheduler(k)
    k.scheduler = sched

    def _start(_svc: Any = None) -> None:
        sched.start()

    async def _stop(_svc: Any = None) -> None:
        await sched.stop()

    if hasattr(svc, "on_start") and isinstance(svc.on_start, list):
        svc.on_start.append(_start)
        if hasattr(svc, "on_stop") and isinstance(svc.on_stop, list):
            svc.on_stop.append(_stop)
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        log.warning("kernel.wiring: no running loop and no on_start hook; scheduler not started")
        return
    sched.start()
    # stop with the Kernel (harnesses without on_stop hooks)
    orig_stop = k.stop

    async def stop_all() -> None:
        await sched.stop()
        await orig_stop()

    k.stop = stop_all
