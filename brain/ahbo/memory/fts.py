"""FTS5 helpers (§7.1). Chinese text is indexed with the trigram tokenizer, which needs terms of ≥ 3 characters;
shorter terms are searched with LIKE on the content table instead.

Untrusted text (her words, model output) never reaches the MATCH grammar as syntax: every term is wrapped as an
FTS5 string ("…", with `"` doubled), so operators like AND/OR/NEAR, `*`, `^`, `:` and parentheses inside it are
plain characters. LIKE patterns escape `%`, `_` and the escape character itself.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import unicodedata
from collections.abc import Iterable
from typing import Any

from ahbo.core.db import Database

log = logging.getLogger("ahbo.memory.fts")

LIKE_ESCAPE = "\\"
# A "word run": CJK ideographs, letters and digits. Everything else (punctuation, spaces, emoji) splits runs.
_RUN_RE = re.compile(r"[0-9A-Za-z㐀-䶿一-鿿豈-﫿\U00020000-\U0002ffff]+")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def clean(text: str | None) -> str:
    """NFKC-normalise (full-width → half-width) and drop control characters (NUL would break SQLite strings)."""
    if not text:
        return ""
    return _CTRL_RE.sub(" ", unicodedata.normalize("NFKC", str(text))).strip()


def phrase(term: str) -> str:
    """One FTS5 string token: the term is matched literally, whatever it contains."""
    return '"' + term.replace('"', '""') + '"'


def match_any(terms: Iterable[str]) -> str | None:
    """A MATCH expression that ORs literal phrases of ≥ 3 characters (shorter ones can't match a trigram index)."""
    ts = []
    for t in terms:
        t = clean(t)
        if len(t) >= 3 and t not in ts:
            ts.append(t)
    return " OR ".join(phrase(t) for t in ts) if ts else None


def like_pattern(term: str) -> str:
    """`%term%` with LIKE wildcards escaped (use with `ESCAPE '\\'`)."""
    esc = term.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2).replace("%", LIKE_ESCAPE + "%").replace("_", LIKE_ESCAPE + "_")
    return f"%{esc}%"


def runs(text: str) -> list[str]:
    return _RUN_RE.findall(clean(text))


def ngrams(run: str, n: int) -> list[str]:
    if len(run) < n:
        return []
    return [run[i:i + n] for i in range(len(run) - n + 1)]


def fts_search(db: Database, fts_table: str, base_table: str, terms: Iterable[str], *, where: str = "1",
               params: tuple[Any, ...] = (), columns: str = "b.*", limit: int = 50) -> list[dict[str, Any]]:
    """Rows of `base_table` (alias `b`) whose FTS row matches any of `terms` (≥ 3 chars), best bm25 first.
    `fts_table`/`base_table`/`where` are code constants, never user text. Returns [] on any FTS error."""
    expr = match_any(terms)
    if not expr:
        return []
    sql = (f"SELECT {columns}, bm25({fts_table}) AS _rank FROM {fts_table} JOIN {base_table} b "
           f"ON b.rowid = {fts_table}.rowid WHERE {fts_table} MATCH ? AND ({where}) ORDER BY _rank LIMIT ?")
    try:
        return db.query(sql, (expr, *params, int(limit)))
    except sqlite3.Error:     # defensive: a malformed expression must degrade to "no FTS hits", never crash
        log.warning("fts query failed on %s", fts_table, exc_info=True)
        return []


def like_search(db: Database, base_table: str, column: str, terms: Iterable[str], *, where: str = "1",
                params: tuple[Any, ...] = (), columns: str = "b.*", limit: int = 50) -> list[dict[str, Any]]:
    """Rows of `base_table` whose `column` contains any of `terms` (any length ≥ 1), via escaped LIKE."""
    ts = [t for t in dict.fromkeys(clean(t) for t in terms) if t]
    if not ts:
        return []
    cond = " OR ".join(f"b.{column} LIKE ? ESCAPE '{LIKE_ESCAPE}'" for _ in ts)
    sql = f"SELECT {columns} FROM {base_table} b WHERE ({cond}) AND ({where}) LIMIT ?"
    return db.query(sql, (*[like_pattern(t) for t in ts], *params, int(limit)))
