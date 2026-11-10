from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from ahbo.core.jsonutil import dumps
from ahbo.memory import life_stories as ls
from ahbo.memory import retention, store


def test_pick_lru_with_variety(db, clock):
    seen = []
    for _ in range(3):
        s = ls.pick_life_story(db, clock)
        assert s and s["id"] not in seen
        seen.append(s["id"])
        assert ls.mark_story_used(db, clock, s["id"])
        clock.advance(60)
    # all used once: the oldest comes back, never the one just used
    s = ls.pick_life_story(db, clock)
    assert s["id"] == seen[0]


def test_pick_topic_and_guardrail(db, clock):
    s = ls.pick_life_story(db, clock, topic="粵曲")
    assert s["title"] == "睇大戲" and s["topic"] == "opera"
    assert ls.pick_life_story(db, clock, topic="opera")["title"] == "睇大戲"
    sid = ls.add_life_story(db, clock, "老公生前最鍾意帶佢去淺水灣游水。", photo_id="ph_1")
    row = db.one("SELECT * FROM life_stories WHERE id=?", (sid,))
    assert row["ok_to_reminisce"] == 0 and row["title"] == "老公生前最鍾意帶佢去淺水灣"[:12]
    for _ in range(6):
        p = ls.pick_life_story(db, clock)
        assert p["id"] != sid
        ls.mark_story_used(db, clock, p["id"])
        clock.advance(60)
    assert ls.set_ok_to_reminisce(db, sid, True)
    p = ls.pick_life_story(db, clock, topic="淺水灣")
    assert p["id"] == sid and p["photo_url"] == "/media/photo/ph_1"
    db.execute("UPDATE life_stories SET ok_to_reminisce=0")
    assert ls.pick_life_story(db, clock) is None


def test_add_story_rejects_pii(db, clock):
    with pytest.raises(store.MemoryRejected):
        ls.add_life_story(db, clock, "佢張身份證係A123456(7)")
    with pytest.raises(store.MemoryRejected):
        ls.add_life_story(db, clock, "   ")
    sid = ls.add_life_story(db, clock, "後生時喺工廠做車衣。", title="做車衣", ok_to_reminisce=True, era="1960s")
    assert db.one("SELECT topic FROM life_stories WHERE id=?", (sid,))["topic"] == "work"


def _seed_files(db, clock, data_dir: Path):
    now = clock.now()
    old, new = (now - timedelta(days=31)).isoformat(), (now - timedelta(days=1)).isoformat()
    week = (now - timedelta(days=8)).isoformat()
    (data_dir / "media").mkdir(parents=True)
    files = {n: data_dir / "media" / n for n in ("old.m4a", "new.m4a", "old.jpg", "new.jpg", "story.jpg", "t.txt")}
    for f in files.values():
        f.write_bytes(b"x")
    db.execute("INSERT INTO voice_messages(id,file_path,received_at) VALUES ('vm_old',?,?)", (str(files["old.m4a"]), old))
    db.execute("INSERT INTO voice_messages(id,file_path,received_at) VALUES ('vm_new',?,?)", (str(files["new.m4a"]), new))
    db.execute("INSERT INTO media(id,kind,path,created_at) VALUES ('ph_old','photo',?,?)", (str(files["old.jpg"]), week))
    db.execute("INSERT INTO media(id,kind,path,created_at) VALUES ('ph_new','photo',?,?)", (str(files["new.jpg"]), new))
    db.execute("INSERT INTO media(id,kind,path,created_at) VALUES ('ph_story','photo',?,?)", (str(files["story.jpg"]), old))
    db.execute("INSERT INTO life_stories(id,title,text,photo_id) VALUES ('st','相','一張舊相','ph_story')")
    db.execute("INSERT INTO turns(id,session_id,ts,role,text) VALUES ('t_old','s',?,'user','三十日前講嘅嘢')", (old,))
    db.execute("INSERT INTO turns(id,session_id,ts,role,text) VALUES ('t_new','s',?,'user','琴日講嘅嘢')", (new,))
    db.execute("INSERT INTO sessions(id,opened_at,transcript_ref,summary,summary_text) VALUES ('s_old',?,?,?,?)",
               (old, "media/t.txt", dumps(["舊"]), "舊"))
    return files


def test_retention(db, clock, tmp_path):
    files = _seed_files(db, clock, tmp_path)
    seed_vm = db.one("SELECT file_path FROM voice_messages WHERE id='vm_demo'")["file_path"]
    db.execute("UPDATE voice_messages SET received_at=? WHERE id='vm_demo'", ((clock.now() - timedelta(days=40)).isoformat(),))
    out = retention.run_retention(db, clock, tmp_path)
    assert out["turns"] == 1 and out["voice_messages"] == 2 and out["photos"] == 1 and out["transcripts"] == 1
    assert not files["old.m4a"].exists() and files["new.m4a"].exists()
    assert not files["old.jpg"].exists() and files["new.jpg"].exists() and files["story.jpg"].exists()
    assert not files["t.txt"].exists()
    ids = {r["id"] for r in db.query("SELECT id FROM turns")}
    assert ids == {"t_new"}
    assert db.query("SELECT rowid FROM turns_fts WHERE turns_fts MATCH ?", ('"三十日"',)) == []
    # the seed voice file lives in the repo, outside data_dir: row gone, file untouched
    if Path(seed_vm).parent.exists():
        assert not Path(seed_vm).exists() or Path(seed_vm).is_file()
    assert db.one("SELECT summary_text FROM sessions WHERE id='s_old'")["summary_text"] == "舊"   # summaries kept
    assert retention.run_retention(db, clock, tmp_path) == {"turns": 0, "transcripts": 0, "voice_messages": 0,
                                                            "photos": 0, "files": 0}


def test_unlink_refuses_outside_data_dir(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    data = tmp_path / "data"
    data.mkdir()
    assert retention._unlink(str(outside), data) is False
    assert retention._unlink("../outside.txt", data) is False
    assert outside.exists()


def test_export_and_wipe(db, clock, tmp_path):
    files = _seed_files(db, clock, tmp_path)
    ex = retention.export_all(db)
    assert "facts" in ex["tables"] and not any("_fts" in t for t in ex["tables"])
    assert len(ex["tables"]["facts"]) == 6
    import json
    json.dumps(ex, ensure_ascii=False, default=str)
    counts = retention.wipe(db, tmp_path)
    assert counts["facts"] == 6 and counts["files"] >= 5
    for t in ("facts", "sessions", "turns", "life_stories", "voice_messages", "media", "events", "kv", "contacts"):
        assert db.one(f"SELECT COUNT(*) n FROM {t}")["n"] == 0, t
    assert db.one("SELECT value FROM meta WHERE key='schema_version'")
    assert store.recall(db, "阿珍") == {"facts": [], "episodes": []}
    assert db.query("SELECT rowid FROM facts_fts WHERE facts_fts MATCH ?", ('"將軍澳"',)) == []
    assert not any(f.exists() for f in files.values())
