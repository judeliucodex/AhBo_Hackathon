"""Retention, export and wipe (§11 PII/PDPO posture).

- `run_retention`: transcripts (turns) 30 days, voice notes 30 days, photos 7 days — files and rows. Called once
  per HKT day by `wiring` (on the scheduler's tick) and callable directly by any daily job.
  Photos attached to family life stories are kept (they're the family's, not camera captures).
- `export_all(db)`: every table as JSON-ready rows (`ahbo admin export`).
- `wipe(db)`: deletes every personal-data row and the media files the DB points at (`ahbo admin wipe`).

Files are only ever deleted when they resolve inside the data directory: seed/demo assets in the repo are never
touched, whatever a row's path says.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ahbo.core.clock import Clock
from ahbo.core.db import SCHEMA_VERSION, Database

log = logging.getLogger("ahbo.memory.retention")

TRANSCRIPT_DAYS = 30
VOICE_DAYS = 30
PHOTO_DAYS = 7
PHOTO_KINDS = ("photo", "incident_photo", "camera")
KEEP_TABLES = ("meta",)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _unlink(path_str: str | None, data_dir: Path | None) -> bool:
    if not path_str or data_dir is None:
        return False
    p = Path(path_str)
    if not p.is_absolute():
        p = data_dir / p
    if not _inside(p, data_dir):
        return False
    try:
        p.unlink(missing_ok=True)
        return True
    except OSError:
        log.warning("could not delete %s", p.name)
        return False


def run_retention(db: Database, clock: Clock, data_dir: Path | None = None, *, transcript_days: int = TRANSCRIPT_DAYS,
                  voice_days: int = VOICE_DAYS, photo_days: int = PHOTO_DAYS) -> dict[str, int]:
    """Delete expired transcripts, voice notes and photos. Returns counts."""
    now = clock.now()
    out = {"turns": 0, "transcripts": 0, "voice_messages": 0, "photos": 0, "files": 0}

    cut = now - timedelta(days=transcript_days)
    old_turns = [r["id"] for r in db.query("SELECT id, ts FROM turns") if (_parse(r["ts"]) or now) < cut]
    for i in range(0, len(old_turns), 500):
        chunk = old_turns[i:i + 500]
        db.execute(f"DELETE FROM turns WHERE id IN ({','.join('?' * len(chunk))})", chunk)
    out["turns"] = len(old_turns)
    for r in db.query("SELECT id, opened_at, transcript_ref FROM sessions WHERE transcript_ref IS NOT NULL"):
        if (_parse(r["opened_at"]) or now) < cut:
            out["files"] += _unlink(r["transcript_ref"], data_dir)
            db.execute("UPDATE sessions SET transcript_ref=NULL WHERE id=?", (r["id"],))
            out["transcripts"] += 1

    cut = now - timedelta(days=voice_days)
    for r in db.query("SELECT id, file_path, received_at FROM voice_messages"):
        if (_parse(r["received_at"]) or now) < cut:
            out["files"] += _unlink(r["file_path"], data_dir)
            db.execute("DELETE FROM voice_messages WHERE id=?", (r["id"],))
            out["voice_messages"] += 1

    cut = now - timedelta(days=photo_days)
    story_photos = {r["photo_id"] for r in db.query("SELECT photo_id FROM life_stories WHERE photo_id IS NOT NULL")}
    for r in db.query("SELECT id, kind, path, created_at, expires_at FROM media"):
        if r["id"] in story_photos:
            continue
        exp = _parse(r["expires_at"])
        expired = (exp is not None and exp <= now) or (r["kind"] in PHOTO_KINDS and (_parse(r["created_at"]) or now) < cut)
        if expired:
            out["files"] += _unlink(r["path"], data_dir)
            db.execute("DELETE FROM media WHERE id=?", (r["id"],))
            out["photos"] += 1
    if any(out.values()):
        log.info("retention: %s", out)
    return out


def _tables(db: Database) -> list[str]:
    rows = db.query("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    return [r["name"] for r in rows if "_fts" not in r["name"]]


def export_all(db: Database) -> dict[str, Any]:
    """Every row of every table (FTS indexes excluded: they are derived)."""
    return {"schema_version": SCHEMA_VERSION,
            "tables": {t: db.query(f'SELECT * FROM "{t}"') for t in _tables(db)}}


def _wipe_public(data_dir: Path) -> int:
    """Delete `data/media/public/` (the P1 phone clip: her name, address and the fall notice, QA 26 D1). Its URL was
    disclosed to Twilio and the route serves it without auth, so nothing of it may survive a wipe."""
    root = data_dir / "media" / "public"
    if not root.is_dir() or root.is_symlink() or not _inside(root, data_dir):
        return 0
    n = 0
    for p in sorted(root.rglob("*"), key=lambda x: len(x.parts), reverse=True):
        try:
            if p.is_dir() and not p.is_symlink():
                p.rmdir()
            else:
                p.unlink(missing_ok=True)
                n += 1
        except OSError:
            log.warning("could not delete %s", p.name)
    return n


def wipe(db: Database, data_dir: Path | None = None) -> dict[str, int]:
    """Delete all personal data: every table except `meta`, plus media/voice/transcript files inside data_dir and the
    whole `media/public/` folder (the public phone clip).
    Uses secure_delete and VACUUM so the rows don't linger in free pages."""
    files = 0
    if data_dir is not None:
        paths = [r["path"] for r in db.query("SELECT path FROM media")]
        paths += [r["file_path"] for r in db.query("SELECT file_path FROM voice_messages")]
        paths += [r["transcript_ref"] for r in db.query("SELECT transcript_ref FROM sessions WHERE transcript_ref IS NOT NULL")]
        files = sum(_unlink(p, data_dir) for p in paths)
        files += _wipe_public(data_dir)
    counts: dict[str, int] = {}
    db.execute("PRAGMA secure_delete=ON")
    with db.transaction():
        for t in _tables(db):
            if t in KEEP_TABLES:
                continue
            counts[t] = db.execute(f'DELETE FROM "{t}"').rowcount
        for f in ("facts_fts", "sessions_fts", "turns_fts", "life_stories_fts"):
            db.execute(f"INSERT INTO {f}({f}) VALUES ('rebuild')")
    try:
        db.execute("VACUUM")
    except Exception:      # e.g. another connection holds a read lock; data is deleted either way
        log.warning("vacuum after wipe failed", exc_info=True)
    counts["files"] = files
    return counts
