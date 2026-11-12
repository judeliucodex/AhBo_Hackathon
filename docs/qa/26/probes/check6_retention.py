"""QA26 check 6: retention — photos > 7 days, voice notes and transcripts > 30 days deleted (files+rows);
seed files (outside the data dir) never deleted; life-story photos exempt (BUILD_LOG D-15)."""
import sys
from datetime import timedelta
from pathlib import Path

from ahbo.core.clock import FakeClock, set_clock
from ahbo.core.db import open_db
from ahbo.memory.retention import run_retention

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


clock = FakeClock("2026-11-20T10:00:00+08:00")
set_clock(clock)
db = open_db(":memory:")
data = Path("/scratch/ret-data")
data.mkdir(parents=True, exist_ok=True)
now = clock.now()

def f(name, content=b"x"):
    p = data / name
    p.write_bytes(content)
    return str(p)

def media(mid, kind, path, age_days, expires=None):
    db.execute("INSERT INTO media(id, kind, path, incident_id, created_at, expires_at) VALUES (?,?,?,?,?,?)",
               (mid, kind, path, None, (now - timedelta(days=age_days)).isoformat(), expires))

# photos: > 7 days gone, ≤ 7 days kept, expires_at honoured, story photo exempt
media("ph_old", "photo", f("photo_old.jpg"), 8)
media("ph_new", "photo", f("photo_new.jpg"), 1)
media("ph_exp", "photo", f("photo_expired.jpg"), 1, expires=(now - timedelta(hours=1)).isoformat())
media("ph_story", "photo", f("story_photo.jpg"), 8)
db.execute("INSERT INTO life_stories(id, title, text, photo_id) VALUES ('ls_1', '舊時', '以前…', 'ph_story')")
media("ph_edge", "photo", f("photo_7d.jpg"), 7)          # exactly 7 days: kept (strictly older is deleted)

# voice notes: > 30 days gone, ≤ 30 kept
def vm(vid, path, age_days):
    db.execute("INSERT INTO voice_messages(id, direction, file_path, received_at) VALUES (?,?,?,?)",
               (vid, "in", path, (now - timedelta(days=age_days)).isoformat()))
vm("vm_old", f("voice_old.m4a"), 31)
vm("vm_new", f("voice_new.m4a"), 29)

# transcripts: sessions > 30 days lose the file + the ref; turns > 30 days deleted
def sess(sid, age_days, ref):
    db.execute("INSERT INTO sessions(id, opened_at, transcript_ref) VALUES (?,?,?)",
               (sid, (now - timedelta(days=age_days)).isoformat(), ref))
sess("s_old", 31, f("transcript_old.txt"))
sess("s_new", 1, f("transcript_new.txt"))
db.execute("INSERT INTO turns(id, session_id, ts, role, text) VALUES ('t_old', 's_old', ?, 'user', '舊對話')",
           ((now - timedelta(days=31)).isoformat(),))
db.execute("INSERT INTO turns(id, session_id, ts, role, text) VALUES ('t_new', 's_new', ?, 'user', '新對話')",
           ((now - timedelta(days=1)).isoformat(),))

# a 'seed' file OUTSIDE the data dir: a real repo seed file (read-only mount) must never be touched
seed_path = "/repo/brain/seed/fall_placeholder.jpg"
media("ph_seed", "photo", seed_path, 8)                  # old enough to be deleted if the guard is broken
check("repo seed file exists before", Path(seed_path).is_file(), seed_path)

out = run_retention(db, clock, data)
print("retention counts:", out)

check("photo >7d deleted (row)", db.one("SELECT COUNT(*) n FROM media WHERE id='ph_old'")["n"] == 0)
check("photo >7d file deleted", not (data / "photo_old.jpg").exists())
check("photo ≤7d kept", db.one("SELECT COUNT(*) n FROM media WHERE id='ph_new'")["n"] == 1 and (data / "photo_new.jpg").exists())
check("photo exactly 7d old kept (strictly-older rule)",
      db.one("SELECT COUNT(*) n FROM media WHERE id='ph_edge'")["n"] == 1 and (data / "photo_7d.jpg").exists())
check("photo with past expires_at deleted", db.one("SELECT COUNT(*) n FROM media WHERE id='ph_exp'")["n"] == 0
      and not (data / "photo_expired.jpg").exists())
check("life-story photo kept (D-15)", db.one("SELECT COUNT(*) n FROM media WHERE id='ph_story'")["n"] == 1
      and (data / "story_photo.jpg").exists())
check("voice >30d deleted", db.one("SELECT COUNT(*) n FROM voice_messages WHERE id='vm_old'")["n"] == 0
      and not (data / "voice_old.m4a").exists())
check("voice ≤30d kept", db.one("SELECT COUNT(*) n FROM voice_messages WHERE id='vm_new'")["n"] == 1
      and (data / "voice_new.m4a").exists())
check("old turn deleted, new turn kept",
      db.one("SELECT COUNT(*) n FROM turns WHERE id='t_old'")["n"] == 0
      and db.one("SELECT COUNT(*) n FROM turns WHERE id='t_new'")["n"] == 1)
check("old session transcript file deleted + ref cleared",
      not (data / "transcript_old.txt").exists()
      and db.one("SELECT transcript_ref FROM sessions WHERE id='s_old'")["transcript_ref"] is None)
check("new session transcript kept", (data / "transcript_new.txt").exists())
check("REPO SEED FILE still there (never deleted)", Path(seed_path).is_file(), seed_path)
check("outside-path row itself was removed from media",
      db.one("SELECT COUNT(*) n FROM media WHERE id='ph_seed'")["n"] == 0)

print(f"\n{'ALL CHECKS PASS' if not FAILS else 'CHECK FAILURES: ' + '; '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
