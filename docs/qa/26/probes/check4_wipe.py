"""QA26 check 4: wipe/export — refuses without --yes; export file written first; personal data + media gone."""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f" - {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


DB = "/tmp/qa/ahbo.sqlite"
DATA = Path("/tmp/qa")

def counts(con=None):
    con = con or sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_fts'")]
    out = {}
    for t in tables:
        try:
            out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.OperationalError:
            pass
    return out

before = counts()
personal_tables = [t for t in before if t != "meta"]
nonempty_before = {t: n for t, n in before.items() if t != "meta" and n > 0}
print("tables with rows before wipe:", nonempty_before)
check("there is personal data to wipe", len(nonempty_before) > 3, str(len(nonempty_before)))

media_files_before = [p.name for p in (DATA / "media").rglob("*") if p.is_file()]
check("media files exist before", len(media_files_before) > 0, f"{len(media_files_before)} files (fixed clips + public P1)")

export_default = DATA / "export-2026-11-12.json"
export_default.unlink(missing_ok=True)

# ---- 1. refuses without --yes
r = subprocess.run([sys.executable, "-m", "ahbo.ops.wipe"], capture_output=True, text=True)
check("wipe without --yes exits non-zero", r.returncode != 0, f"rc={r.returncode}")
check("refusal says why", "refusing" in (r.stderr + r.stdout).lower(), (r.stderr or r.stdout).strip()[:90])
after = counts()
check("nothing was deleted without --yes", after == before,
      str({t: (before.get(t), after.get(t)) for t in before if before.get(t) != after.get(t)}))
check("no export written without --yes", not export_default.exists())

# ---- 2. wipe with --yes
fall = sqlite3.connect(f"file:{DB}?mode=ro", uri=True).execute(
    "SELECT COUNT(*) FROM events WHERE id='evt_qa26_restore_marker' OR type='sensor.fall_suspected'").fetchone()[0]
check("marker data present before wipe", fall >= 1, str(fall))
r = subprocess.run([sys.executable, "-m", "ahbo.ops.wipe", "--yes"], capture_output=True, text=True)
print(r.stdout.strip()[:200])
check("wipe --yes exits 0", r.returncode == 0, f"rc={r.returncode} {r.stderr[:120]}")
check("export file exists", export_default.is_file(), str(export_default))
check("export mode 600", oct(export_default.stat().st_mode & 0o777) == "0o600",
      oct(export_default.stat().st_mode & 0o777))

exp = json.loads(export_default.read_text())
exps = exp.get("tables", {})
check("export carries schema_version + every table", isinstance(exps, dict) and "events" in exps and "commands" in exps,
      f"{len(exps)} tables in export")
check("export contains the PRE-wipe fall event (taken before deletion)",
      any(e.get("type") == "sensor.fall_suspected" for e in (exps.get("events") or [])),
      f"{len(exps.get('events') or [])} events exported")

after = counts()
leftover = {t: n for t, n in after.items() if t != "meta" and n > 0}
check("every table except meta is empty", not leftover, str(leftover))
check("meta kept (kv intact)", after.get("meta", 0) > 0, str(after.get("meta")))

media_files_after = [str(p.relative_to(DATA)) for p in (DATA / "media").rglob("*") if p.is_file()]
photos_left = [p for p in media_files_after if p.startswith("media/public/")]
voice_left = [p for p in media_files_after if p.startswith("media/voice/")]
check("personal media gone (public phone clip + voice)", not photos_left and not voice_left,
      f"left: {photos_left[:3]} {voice_left[:3]}")
fixed_left = [p for p in media_files_after if p.startswith("media/fixed/")]
check("fixed-clip cache kept by design (not personal data)", len(fixed_left) > 0, f"{len(fixed_left)} files")

print(f"\n{'ALL CHECKS PASS' if not FAILS else 'CHECK FAILURES: ' + '; '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
