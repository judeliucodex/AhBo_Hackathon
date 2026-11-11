"""ops.wipe (§11) and ops.backup: export→wipe via memory.retention; sqlite3 backup API with pruning."""
from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from ahbo.core.clock import HKT, FakeClock, get_clock, set_clock
from ahbo.core.db import open_db
from ahbo.core.settings import load_settings
from ahbo.ops.backup import backup, next_nightly_seconds
from ahbo.ops.seed import load_demo
from ahbo.ops.wipe import main


def setup_function(_: object) -> None:
    set_clock(FakeClock("2026-11-20T10:00:00+08:00"))


def _db_with_data(tmp_path: Path):
    settings = dataclasses.replace(load_settings(), data_dir=tmp_path)
    db = open_db(tmp_path / "ahbo.sqlite")
    load_demo(db, get_clock(), settings)
    photo = tmp_path / "media" / "incidents" / "ph_test.jpg"
    photo.parent.mkdir(parents=True, exist_ok=True)
    photo.write_bytes(b"\xff\xd8\xff-fakejpeg")
    db.execute("INSERT INTO media(id, kind, path, incident_id, created_at) VALUES (?,?,?,?,?)",
               ("ph_test", "photo", str(photo), "inc_1", get_clock().iso()))
    db.kv_set("public_clip_path", "abc123def4567890")
    db.kv_set("clock_offset_s", 60)
    return db, settings


def test_wipe_requires_yes(capsys) -> None:
    assert main([]) == 2                       # refuses before touching settings or the DB
    assert "--yes" in capsys.readouterr().err


def test_wipe_exports_then_deletes_everything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    db, settings = _db_with_data(tmp_path)
    db.close()
    from ahbo.core import settings as settings_mod

    # main() imports get_settings from the module at call time: patch the module attribute
    monkeypatch.setattr(settings_mod, "get_settings", lambda: settings)
    rc = main(["--yes", "--out", str(tmp_path / "export.json")])
    out = capsys.readouterr().out
    assert rc == 0 and "wiped:" in out
    export = json.loads((tmp_path / "export.json").read_text())
    assert "resident" in export["tables"] and "facts" in export["tables"]
    assert export["tables"]["resident"][0]["name_zh"] == "王婆婆"
    assert not (tmp_path / "media" / "incidents" / "ph_test.jpg").exists()      # media file deleted
    db2 = open_db(tmp_path / "ahbo.sqlite")
    assert db2.one("SELECT id FROM facts LIMIT 1") is None                      # rows gone
    assert db2.one("SELECT id FROM media LIMIT 1") is None
    assert db2.one("SELECT key FROM kv WHERE key='public_clip_path'") is None   # kv cleared too
    assert db2.one("SELECT value FROM meta WHERE key='schema_version'") is not None   # meta survives
    db2.close()


def test_backup_creates_mode600_copy_and_prunes(tmp_path: Path) -> None:
    src = tmp_path / "ahbo.sqlite"
    db = open_db(src)
    db.execute("INSERT INTO kv(key, value) VALUES ('x', '1')")
    db.close()
    dest_dir = tmp_path / "backups"
    for stamp in ("20261120-030000", "20261119-030000", "20261118-030000"):
        backup(src, dest_dir, keep=2, stamp=stamp)     # the last call prunes its own (oldest) file away
    names = sorted(p.name for p in dest_dir.glob("ahbo-*.sqlite"))
    assert len(names) == 2                                                      # keep=2 pruned the oldest
    assert "ahbo-20261118-030000.sqlite" not in names
    for p in dest_dir.glob("ahbo-*.sqlite"):
        assert oct(p.stat().st_mode & 0o777) == "0o600"                          # transcripts: never world-readable
    con = sqlite3.connect(str(dest_dir / names[-1]))
    assert con.execute("SELECT value FROM kv WHERE key='x'").fetchone() == ("1",)
    con.close()


def test_backup_of_live_wal_db_is_consistent(tmp_path: Path) -> None:
    """Write through one connection while backing up via another: the copy is a consistent prefix."""
    src = tmp_path / "live.sqlite"
    writer = open_db(src)
    writer.execute("INSERT INTO kv(key, value) VALUES ('a', '1')")
    dest = backup(src, tmp_path / "backups", keep=7)
    writer.execute("INSERT INTO kv(key, value) VALUES ('b', '2')")
    con = sqlite3.connect(str(dest))
    rows = dict(con.execute("SELECT key, value FROM kv"))
    con.close()
    assert rows == {"a": "1"}
    writer.close()


def test_next_nightly_seconds():
    before = datetime(2026, 11, 20, 2, 0, tzinfo=HKT)
    after = datetime(2026, 11, 20, 4, 0, tzinfo=HKT)
    assert next_nightly_seconds(before) == 1.5 * 3600
    assert next_nightly_seconds(after) == 23.5 * 3600
