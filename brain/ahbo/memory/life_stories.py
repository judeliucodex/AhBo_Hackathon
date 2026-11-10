"""Reminiscence (§7.3): family-seeded life stories.

`pick_life_story` chooses the least recently used story with topic variety (not the same topic as the story
used last, when there is a choice), and never one with `ok_to_reminisce = 0` (grief guardrail). `/story` from the
family adds stories with an optional photo; a story that mentions a death is stored with ok_to_reminisce = 0
unless the family explicitly marks it ok.
"""
from __future__ import annotations

from typing import Any

from ahbo.core.clock import Clock
from ahbo.core.db import Database
from ahbo.core.ids import new_id
from ahbo.core.jsonutil import dumps, loads

from . import fts
from .pii import find_pii
from .store import MemoryRejected

DECEASED_WORDS = ("過身", "去世", "過咗身", "走咗", "已故", "生前", "死咗", "離世", "仙遊", "安息", "喪禮", "出殯")
TOPIC_WORDS: dict[str, tuple[str, ...]] = {
    "work": ("教書", "學生", "老師", "返工", "工廠", "學校"),
    "opera": ("粵曲", "大戲", "帝女花", "任白", "任劍輝", "白雪仙", "利舞臺", "戲院"),
    "family": ("阿明", "阿珍", "孫", "女兒", "仔", "結婚", "老公"),
    "food": ("飲茶", "蝦餃", "煮飯", "餸", "酒樓"),
    "travel": ("旅行", "去咗", "坐船", "火車"),
}


def infer_topic(text: str) -> str | None:
    best, hits = None, 0
    for topic, words in TOPIC_WORDS.items():
        n = sum(1 for w in words if w in text)
        if n > hits:
            best, hits = topic, n
    return best


def _topic(row: dict[str, Any]) -> str:
    return row.get("topic") or infer_topic(f"{row['title']}{row['text']}") or (row.get("era") or "") or row["id"]


def _public(row: dict[str, Any]) -> dict[str, Any]:
    out = {"id": row["id"], "title": row["title"], "text": row["text"], "era": row.get("era"),
           "topic": _topic(row), "photo_id": row.get("photo_id"), "people": loads(row.get("people"), []) or []}
    if row.get("photo_id"):
        out["photo_url"] = f"/media/photo/{row['photo_id']}"
    return out


def pick_life_story(db: Database, clock: Clock, topic: str | None = None) -> dict[str, Any] | None:
    """Least recently used story (never-used first), avoiding the last-used topic when another is available.
    With `topic`, stories matching it (topic key or words in title/text) are preferred."""
    rows = db.query("SELECT rowid AS _rowid, * FROM life_stories WHERE ok_to_reminisce = 1")
    if not rows:
        return None
    if topic:
        t = fts.clean(topic)
        keys = {t, infer_topic(t)} - {None, ""}
        matched = [r for r in rows if _topic(r) in keys or (t and (t in r["title"] or t in r["text"]))]
        if not matched and t:
            ids = {r["id"] for r in fts.fts_search(db, "life_stories_fts", "life_stories", [t],
                                                   where="b.ok_to_reminisce = 1", columns="b.id")}
            matched = [r for r in rows if r["id"] in ids]
        rows = matched or rows
    used = db.one("SELECT * FROM life_stories WHERE last_used_at IS NOT NULL ORDER BY last_used_at DESC LIMIT 1")
    last_topic = _topic(used) if used else None
    rows.sort(key=lambda r: (r["last_used_at"] is not None, r["last_used_at"] or "", r["_rowid"]))
    varied = [r for r in rows if _topic(r) != last_topic]
    return _public((varied or rows)[0])


def mark_story_used(db: Database, clock: Clock, story_id: str) -> bool:
    cur = db.execute("UPDATE life_stories SET last_used_at=? WHERE id=?", (clock.iso(), story_id))
    return cur.rowcount > 0


def add_life_story(db: Database, clock: Clock, text: str, *, title: str | None = None, photo_id: str | None = None,
                   era: str | None = None, topic: str | None = None, people: list[str] | None = None,
                   ok_to_reminisce: bool | None = None) -> str:
    """`/story <text>` (+ photo). Raises MemoryRejected on empty text or PII."""
    body = fts.clean(text)
    if not body:
        raise MemoryRejected("empty")
    if len(body) > 2000:
        raise MemoryRejected("too_long")
    kinds = find_pii(body + (title or ""))
    if kinds:
        raise MemoryRejected("pii", ",".join(kinds))
    if not title:
        head = body
        for p in "。，！？,.!?\n":
            head = head.split(p)[0]
        title = head[:12] or body[:12]
    if ok_to_reminisce is None:
        ok_to_reminisce = not any(w in body for w in DECEASED_WORDS)
    sid = new_id("story")
    db.execute("INSERT INTO life_stories(id,title,era,people,text,photo_id,ok_to_reminisce,topic) VALUES (?,?,?,?,?,?,?,?)",
               (sid, fts.clean(title)[:40], era, dumps(people or []), body, photo_id, int(bool(ok_to_reminisce)),
                topic or infer_topic(body)))
    return sid


def set_ok_to_reminisce(db: Database, story_id: str, ok: bool) -> bool:
    return db.execute("UPDATE life_stories SET ok_to_reminisce=? WHERE id=?", (int(ok), story_id)).rowcount > 0
