"""SQLite backup (`ahbo backup` / library use, §26.1): the sqlite3 online-backup API into data/backups/, keep 7.

`api/backup.py` runs the nightly loop (03:30 HKT) and has its own copy of this logic; see the hand-off report —
that module should call this one instead of keeping a second implementation. Safe to run against the live DB:
`sqlite3.Connection.backup` copies under a read lock, WAL writers keep going.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from ahbo.core.clock import HKT

KEEP = 7
STAMP = "%Y%m%d-%H%M%S"


def backup(db_path: Path | str, dest_dir: Path | str, keep: int = KEEP, stamp: str | None = None) -> Path:
    """Copy `db_path` to `dest_dir/ahbo-<stamp HKT>.sqlite` (mode 600), prune to `keep` newest. → the new file."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now(HKT).strftime(STAMP)
    dest = dest_dir / f"ahbo-{stamp}.sqlite"
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)                     # online backup: consistent copy even while the api writes
        finally:
            dst.close()
    finally:
        src.close()
    dest.chmod(0o600)                            # the DB holds transcripts: never world-readable
    _prune(dest_dir, keep)
    return dest


def _prune(dest_dir: Path, keep: int) -> None:
    olds = sorted(dest_dir.glob("ahbo-*.sqlite"))
    for old in olds[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)


def next_nightly_seconds(now: datetime | None = None, at_hour: int = 3, at_minute: int = 30) -> float:
    """Seconds until the next 03:30 HKT (what the nightly loop sleeps for; §3.3)."""
    n = now or datetime.now(HKT)
    nxt = n.replace(hour=at_hour, minute=at_minute, second=0, microsecond=0)
    if nxt <= n:
        nxt += timedelta(days=1)
    return (nxt - n).total_seconds()


def main(argv: list[str] | None = None) -> int:
    from ahbo.core.settings import get_settings

    p = argparse.ArgumentParser(prog="ahbo backup", description="backup the DB now (sqlite3 backup API)")
    p.add_argument("--keep", type=int, default=KEEP, help="how many backups to keep (default 7)")
    a = p.parse_args(argv)
    settings = get_settings()
    dest = backup(settings.db_path, settings.data_dir / "backups", keep=a.keep)
    print(f"backup written: {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
