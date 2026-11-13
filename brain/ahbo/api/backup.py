"""Nightly SQLite backup (§3.3, §26.1 rule 4): the api runs `nightly_loop`, which calls `ahbo.ops.backup.backup`
(the one implementation: sqlite3 online-backup API into data/backups/, mode 600, keep 7) at 03:30 real HKT.
`backup_now` is a thin alias kept for existing callers (Makefile history, tests)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from ahbo.core.clock import HKT
from ahbo.ops.backup import KEEP, backup, next_nightly_seconds

log = logging.getLogger("ahbo.backup")
AT_HOUR = 3            # 03:30 real HKT (not the virtual clock)
AT_MINUTE = 30

__all__ = ["KEEP", "backup_now", "nightly_loop"]


def backup_now(db_path: Path, out_dir: Path, keep: int = KEEP, stamp: str | None = None) -> Path:
    """Alias of `ahbo.ops.backup.backup` (one source of truth)."""
    return backup(db_path, out_dir, keep=keep, stamp=stamp)


def _seconds_until_next(now: datetime) -> float:
    return next_nightly_seconds(now, AT_HOUR, AT_MINUTE)


async def nightly_loop(db_path: Path, out_dir: Path) -> None:
    while True:
        await asyncio.sleep(_seconds_until_next(datetime.now(HKT)))
        try:
            p = await asyncio.to_thread(backup, db_path, out_dir)
            log.info("backup written: %s", p.name)
        except Exception:
            log.exception("nightly backup failed")
