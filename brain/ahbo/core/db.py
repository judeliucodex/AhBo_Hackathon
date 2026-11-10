"""SQLite schema (§14) and connection helper. WAL mode; the `api` process is the single writer.

All writes go through `Database.execute*` (serialised by a lock). Chinese text that is searched has an FTS5
table with the trigram tokenizer (§7.1), kept in sync by triggers.
"""
from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from .jsonutil import dumps, loads

SCHEMA_VERSION = 1

SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- Append-only event log (INV-6)
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, ts TEXT NOT NULL, type TEXT NOT NULL, source TEXT NOT NULL,
  correlation_id TEXT, payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_type ON events(type);
CREATE INDEX IF NOT EXISTS events_corr ON events(correlation_id);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
  BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;

CREATE TABLE IF NOT EXISTS commands (
  id TEXT PRIMARY KEY, event_id TEXT, ts TEXT NOT NULL, type TEXT NOT NULL, target TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 2, correlation_id TEXT, payload TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending',          -- pending|sent|acked|done|failed|dropped
  acked_at TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS commands_corr ON commands(correlation_id);
CREATE INDEX IF NOT EXISTS commands_type ON commands(type);

CREATE TABLE IF NOT EXISTS contacts (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,   -- family|emergency_centre_sim|centre
  relation TEXT, telegram_chat_id TEXT, phone TEXT, verified INTEGER NOT NULL DEFAULT 0,
  priority INTEGER NOT NULL DEFAULT 9, language TEXT DEFAULT 'zh-HK'
);

CREATE TABLE IF NOT EXISTS resident (
  id TEXT PRIMARY KEY, name_zh TEXT, name_en TEXT, age INTEGER, district TEXT, address TEXT,
  shareable TEXT NOT NULL DEFAULT '{}', quiet_hours TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS med_catalogue (
  id TEXT PRIMARY KEY, yue TEXT NOT NULL, en TEXT, dose TEXT, pill TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS med_schedule (
  slot_id TEXT PRIMARY KEY, time TEXT NOT NULL, med_names TEXT NOT NULL,   -- JSON list of catalogue ids
  instructions_text TEXT, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS dose_events (
  id TEXT PRIMARY KEY, slot_id TEXT NOT NULL, date TEXT NOT NULL,
  state TEXT NOT NULL,            -- scheduled|reminding|snoozed|taken_self_reported|missed
  reminded_at TEXT NOT NULL DEFAULT '[]', snoozes INTEGER NOT NULL DEFAULT 0, snoozed_until TEXT,
  resolved_at TEXT, source TEXT, utterance TEXT, updated_at TEXT,
  UNIQUE(slot_id, date)
);

CREATE TABLE IF NOT EXISTS incidents (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL, source TEXT, opened_at TEXT NOT NULL,
  timeline TEXT NOT NULL DEFAULT '[]', photo_id TEXT, vision_note TEXT, acked_by TEXT, closed_at TEXT,
  calls_placed INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL DEFAULT '{}', updated_at TEXT
);

CREATE TABLE IF NOT EXISTS calendar_events (
  id TEXT PRIMARY KEY, datetime TEXT NOT NULL, title TEXT NOT NULL, place TEXT, place_full TEXT,
  kind TEXT NOT NULL DEFAULT 'other',  -- medical|meal|plan|activity|other
  route TEXT, status TEXT NOT NULL DEFAULT 'confirmed',  -- tentative|confirmed|booked|cancelled
  source TEXT, action_id TEXT
);

CREATE TABLE IF NOT EXISTS actions (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, target TEXT, method TEXT, state TEXT NOT NULL,
  details TEXT NOT NULL DEFAULT '{}', question TEXT, approval TEXT, field_map TEXT,
  screenshots TEXT NOT NULL DEFAULT '[]', result TEXT, created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS deep_tasks (
  id TEXT PRIMARY KEY, brief TEXT, goal TEXT, constraints TEXT, status TEXT NOT NULL,
  result TEXT, signup_methods TEXT, cost REAL NOT NULL DEFAULT 0, session_id TEXT,
  started_at TEXT, finished_at TEXT, delivered INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS day_plans (
  date TEXT PRIMARY KEY, json TEXT, summary TEXT, generated_at TEXT, cost REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS facts (
  id TEXT PRIMARY KEY, fact TEXT NOT NULL, category TEXT NOT NULL, importance INTEGER NOT NULL DEFAULT 3,
  status TEXT NOT NULL DEFAULT 'active',    -- active|superseded|pending_review
  source TEXT, confidence REAL, supersedes TEXT, created_at TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(fact, content='facts', content_rowid='rowid', tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
  INSERT INTO facts_fts(rowid, fact) VALUES (new.rowid, new.fact); END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, fact) VALUES ('delete', old.rowid, old.fact); END;
CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, fact) VALUES ('delete', old.rowid, old.fact);
  INSERT INTO facts_fts(rowid, fact) VALUES (new.rowid, new.fact); END;

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, opened_at TEXT NOT NULL, closed_at TEXT, reason TEXT, mode TEXT,
  text_only INTEGER NOT NULL DEFAULT 0, transcript_ref TEXT, summary TEXT NOT NULL DEFAULT '[]',
  summary_text TEXT NOT NULL DEFAULT '', mood TEXT, topics TEXT NOT NULL DEFAULT '[]'
);
CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(summary_text, content='sessions', content_rowid='rowid', tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS sessions_ai AFTER INSERT ON sessions BEGIN
  INSERT INTO sessions_fts(rowid, summary_text) VALUES (new.rowid, new.summary_text); END;
CREATE TRIGGER IF NOT EXISTS sessions_ad AFTER DELETE ON sessions BEGIN
  INSERT INTO sessions_fts(sessions_fts, rowid, summary_text) VALUES ('delete', old.rowid, old.summary_text); END;
CREATE TRIGGER IF NOT EXISTS sessions_au AFTER UPDATE OF summary_text ON sessions BEGIN
  INSERT INTO sessions_fts(sessions_fts, rowid, summary_text) VALUES ('delete', old.rowid, old.summary_text);
  INSERT INTO sessions_fts(rowid, summary_text) VALUES (new.rowid, new.summary_text); END;

CREATE TABLE IF NOT EXISTS turns (
  id TEXT PRIMARY KEY, session_id TEXT, ts TEXT NOT NULL, role TEXT NOT NULL,  -- user|ahbo|system|family
  text TEXT NOT NULL, stt_confidence REAL, latency TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS turns_session ON turns(session_id);
CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(text, content='turns', content_rowid='rowid', tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS turns_ai AFTER INSERT ON turns BEGIN
  INSERT INTO turns_fts(rowid, text) VALUES (new.rowid, new.text); END;
CREATE TRIGGER IF NOT EXISTS turns_ad AFTER DELETE ON turns BEGIN
  INSERT INTO turns_fts(turns_fts, rowid, text) VALUES ('delete', old.rowid, old.text); END;

CREATE TABLE IF NOT EXISTS life_stories (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, era TEXT, people TEXT NOT NULL DEFAULT '[]', text TEXT NOT NULL,
  photo_id TEXT, ok_to_reminisce INTEGER NOT NULL DEFAULT 1, last_used_at TEXT, topic TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS life_stories_fts USING fts5(title, text, content='life_stories', content_rowid='rowid', tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS life_stories_ai AFTER INSERT ON life_stories BEGIN
  INSERT INTO life_stories_fts(rowid, title, text) VALUES (new.rowid, new.title, new.text); END;
CREATE TRIGGER IF NOT EXISTS life_stories_ad AFTER DELETE ON life_stories BEGIN
  INSERT INTO life_stories_fts(life_stories_fts, rowid, title, text) VALUES ('delete', old.rowid, old.title, old.text); END;
CREATE TRIGGER IF NOT EXISTS life_stories_au AFTER UPDATE OF title, text ON life_stories BEGIN
  INSERT INTO life_stories_fts(life_stories_fts, rowid, title, text) VALUES ('delete', old.rowid, old.title, old.text);
  INSERT INTO life_stories_fts(rowid, title, text) VALUES (new.rowid, new.title, new.text); END;

CREATE TABLE IF NOT EXISTS repetition (
  date TEXT NOT NULL, intent TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (date, intent)
);

CREATE TABLE IF NOT EXISTS voice_messages (
  id TEXT PRIMARY KEY, direction TEXT NOT NULL DEFAULT 'in', from_name TEXT, to_name TEXT, contact_id TEXT,
  file_path TEXT, mime TEXT, duration_s REAL, transcript TEXT, received_at TEXT, played_at TEXT,
  telegram_file_id TEXT
);

CREATE TABLE IF NOT EXISTS spans (
  id TEXT PRIMARY KEY, correlation_id TEXT, name TEXT NOT NULL, start_ts TEXT NOT NULL, end_ts TEXT,
  duration_ms REAL, status TEXT NOT NULL DEFAULT 'ok', attrs TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS spans_corr ON spans(correlation_id);

CREATE TABLE IF NOT EXISTS costs (
  id TEXT PRIMARY KEY, ts TEXT NOT NULL, provider TEXT NOT NULL, model TEXT, purpose TEXT,
  units TEXT NOT NULL DEFAULT '{}', usd REAL NOT NULL DEFAULT 0, correlation_id TEXT
);

-- Fake-mode outbox and the record of every real outbound message/call (§26.3)
CREATE TABLE IF NOT EXISTS outbox (
  id TEXT PRIMARY KEY, ts TEXT NOT NULL, channel TEXT NOT NULL,   -- telegram|twilio
  kind TEXT NOT NULL, to_contact TEXT, to_address TEXT, payload TEXT NOT NULL DEFAULT '{}',
  buttons TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'queued', fake INTEGER NOT NULL DEFAULT 1,
  correlation_id TEXT, provider_id TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS media (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, path TEXT NOT NULL, incident_id TEXT, created_at TEXT NOT NULL,
  expires_at TEXT
);

CREATE TABLE IF NOT EXISTS beacons (
  id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, last_seen TEXT, battery REAL, rssi REAL, last_seq INTEGER
);
CREATE TABLE IF NOT EXISTS sensor_seen (beacon_id TEXT NOT NULL, seq INTEGER NOT NULL, PRIMARY KEY (beacon_id, seq));

CREATE TABLE IF NOT EXISTS devices (
  id TEXT PRIMARY KEY, last_seen TEXT, app_version TEXT, fixed_cache_version TEXT, wake_engine TEXT,
  battery REAL, thermal TEXT, online INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS quality (
  id TEXT PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT, correlation_id TEXT
);
"""

# Tables cleared by Reset demo (§12.1). events/commands/spans/costs/quality are kept (trace history).
RESET_TABLES = (
    "contacts", "resident", "med_catalogue", "med_schedule", "dose_events", "incidents", "calendar_events",
    "actions", "deep_tasks", "day_plans", "facts", "sessions", "turns", "life_stories", "repetition",
    "voice_messages", "outbox", "media",
)


class Database:
    """Thin, thread-safe wrapper over one sqlite3 connection (single writer)."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")

    def migrate(self) -> None:
        with self._lock:
            self.conn.executescript(SCHEMA_SQL)
            self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
                              (str(SCHEMA_VERSION),))

    def execute(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, params)

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        with self._lock:
            self.conn.executemany(sql, rows)

    def query(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> dict[str, Any] | None:
        with self._lock:
            r = self.conn.execute(sql, params).fetchone()
            return dict(r) if r else None

    def transaction(self):
        db = self

        class _Tx:
            def __enter__(self_inner):
                db._lock.acquire()
                db.conn.execute("BEGIN IMMEDIATE")
                return db

            def __exit__(self_inner, exc_type, exc, tb):
                try:
                    db.conn.execute("ROLLBACK" if exc_type else "COMMIT")
                finally:
                    db._lock.release()
                return False

        return _Tx()

    # key/value helpers (clock offset, ui_state snapshot, knobs, …)
    def kv_get(self, key: str, default: Any = None) -> Any:
        r = self.one("SELECT value FROM kv WHERE key = ?", (key,))
        return loads(r["value"]) if r else default

    def kv_set(self, key: str, value: Any) -> None:
        self.execute("INSERT INTO kv(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, dumps(value)))

    def close(self) -> None:
        with self._lock:
            self.conn.close()


def open_db(path: Path | str) -> Database:
    db = Database(path)
    db.migrate()
    return db
