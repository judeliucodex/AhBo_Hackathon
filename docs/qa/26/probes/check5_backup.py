"""QA26 check 5: backup/restore — consistent copy (integrity_check), keep 7, restore into a new data dir."""
import os
import sqlite3
import sys
from pathlib import Path

from ahbo.ops.backup import backup

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


LIVE = "/tmp/qa/ahbo.sqlite"
DEST = Path("/tmp/qa/backups")

# 8 backups, distinct stamps -> only 7 kept
made = []
for i in range(8):
    p = backup(LIVE, DEST, keep=7, stamp=f"20261112-0300{i:02d}")
    made.append(p)
kept = sorted(DEST.glob("ahbo-*.sqlite"))
check("only 7 kept (oldest pruned)", len(kept) == 7, f"{[p.name for p in kept]}")
check("pruned the oldest", "ahbo-20261112-030000.sqlite" not in [p.name for p in kept],
      f"first={kept[0].name}")
check("newest kept", "ahbo-20261112-030007.sqlite" in [p.name for p in kept])
mode = oct(made[-1].stat().st_mode & 0o777)
check("backup file mode 600", mode == "0o600", mode)

# consistent copy: integrity + same row counts as the live DB (server idle right now)
con = sqlite3.connect(f"file:{made[-1]}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
ic = con.execute("PRAGMA integrity_check").fetchone()[0]
check("backup PRAGMA integrity_check ok", ic == "ok", str(ic))
live = sqlite3.connect(f"file:{LIVE}?mode=ro", uri=True)
for t in ("events", "commands", "sessions", "turns", "incidents", "contacts", "med_schedule"):
    a = live.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    b = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    # online backup of a live server: the copy is a consistent point-in-time snapshot; the live DB may
    # gain rows (schedule.tick) between the backup and this count, so require b <= a (equal when idle)
    check(f"row count sane for {t} (live={a}, backup={b})", b <= a, f"backup={b}")

# a backup while a writer holds the WAL: do a write from another connection, then back up again
w = sqlite3.connect(LIVE)
w.execute("INSERT OR REPLACE INTO events(id, ts, type, source, correlation_id, payload) VALUES ('evt_qa26_wal', '2026-11-12T03:00:00+08:00', 'quality.probe', 'system', 'evt_qa26_wal', '{}')")
w.commit()
p2 = backup(LIVE, DEST, keep=7, stamp="20261112-030099")
con2 = sqlite3.connect(f"file:{p2}?mode=ro", uri=True)
n = con2.execute("SELECT COUNT(*) FROM events WHERE id='evt_qa26_wal'").fetchone()[0]
check("backup taken during writes contains the committed row", n == 1, str(n))
con2.close()
w.execute("DELETE FROM events WHERE id='evt_qa26_wal' OR id='evt_qa26_restore_marker'")
w.commit()
w.close()
con.close()
live.close()

# seed a marker row so restore proves it carries data, then re-backup for the restore drill
mark = sqlite3.connect(LIVE)
mark.execute("INSERT OR REPLACE INTO events(id, ts, type, source, correlation_id, payload) VALUES ('evt_qa26_restore_marker', '2026-11-12T03:01:00+08:00', 'quality.probe', 'system', 'x', '{}')")
mark.commit()
mark.close()
p3 = backup(LIVE, DEST, keep=7, stamp="20261112-030100")
Path("/scratch/restore_marker").write_text(str(p3))
print(f"\n{'ALL CHECKS PASS' if not FAILS else 'CHECK FAILURES: ' + '; '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
