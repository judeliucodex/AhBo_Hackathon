"""Nightly SQLite backup (§3.3, §26.1 rule 4): `.backup` of the live DB into data/backups/, keep 7."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from ahbo.core.clock import HKT

log = logging.getLogger("ahbo.backup")
KEEP = 7
AT_HOUR = 3            # 03:30 real HKT (not the virtual clock)
AT_MINUTE = 30


def backup_now(db_path: Path, out_dir: Path, keep: int = KEEP, stamp: str | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now(HKT).strftime("%Y%m%d-%H%M%S")
    dest = out_dir / f"ahbo-{stamp}.sqlite"
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    dest.chmod(0o600)
    olds = sorted(out_dir.glob("ahbo-*.sqlite"))
    for old in olds[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)
    return dest


def _seconds_until_next(now: datetime) -> float:
    nxt = now.replace(hour=AT_HOUR, minute=AT_MINUTE, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return (nxt - now).total_seconds()


async def nightly_loop(db_path: Path, out_dir: Path) -> None:
    while True:
        await asyncio.sleep(_seconds_until_next(datetime.now(HKT)))
        try:
            p = await asyncio.to_thread(backup_now, db_path, out_dir)
            log.info("backup written: %s", p.name)
        except Exception:
            log.exception("nightly backup failed")
