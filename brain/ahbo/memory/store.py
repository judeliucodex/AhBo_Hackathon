"""Memory store (§7.1, §7.2, §5.2): profile facts and episodic session summaries.

Public functions (called by tools, the context builder, the Family Link and the digest):
  recall(db, query, limit=5) -> {"facts":[{id,fact,category}], "episodes":[{session_id,date,summary}]}
  remember(db, clock, fact, category, source="voice") -> id          (raises MemoryRejected)
  active_facts(db, limit_tokens) -> [{id,fact,category,importance}]  (ranked by importance, within budget)
  recent_episodes(db, clock, n=3) -> [{session_id,date,summary,lines,mood,topics}]
  add_fact / supersede_fact / pending_review_facts / confirm_fact
  profile_block / episodic_block: ready-made context blocks (§5.2) for the agent's context builder.

Recall uses the trigram FTS5 tables for terms of ≥ 3 characters and LIKE for 1–2 character terms (trigram can't
index them). Day words (琴日, 前日, 上次 …) are expanded to the sessions of that date by the virtual clock.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any

from ahbo.core.clock import Clock, get_clock
from ahbo.core.db import Database
from ahbo.core.ids import new_id
from ahbo.core.jsonutil import loads

from . import fts
from .pii import find_pii

log = logging.getLogger("ahbo.memory")

CATEGORIES = ("family", "health", "life", "preference", "routine", "social", "other")
REVIEW_CATEGORIES = ("family", "health")      # changes need the family's confirmation (§7.2)
TRUSTED_SOURCES = ("seed", "telegram", "carer", "operator")

# Day words → days ago (§7.1 recall expansion). Longest first so 大前日 wins over 前日.
DAY_WORDS: dict[str, int] = {"大前日": 3, "琴日": 1, "尋日": 1, "噚日": 1, "昨日": 1, "昨天": 1, "前日": 2,
                             "今日": 0, "今朝": 0, "今晚": 0, "今天": 0}
RANGE_WORDS: dict[str, int] = {"上個禮拜": 7, "上個星期": 7, "上禮拜": 7, "上星期": 7, "呢幾日": 3, "最近": 3}
LAST_WORDS = ("上次", "頭先", "啱啱", "上一次")

# Common words → extra search terms (synonyms that the facts are actually written with).
EXPANSIONS: dict[str, list[str]] = {
    "個女": ["女兒", "阿珍"], "女兒": ["女兒", "阿珍"], "個仔": ["兒子", "仔"], "孫": ["孫"],
    "醫生": ["醫生", "覆診", "醫院", "診所"], "覆診": ["覆診", "醫院"], "醫院": ["醫院", "覆診"],
    "鍾意": ["鍾意", "最愛"], "好食": ["鍾意", "最愛", "飲茶"], "飲茶": ["飲茶", "蝦餃"],
    "教書": ["教書", "老師"], "做咩工": ["老師", "教書"], "做邊行": ["老師", "教書"], "以前做": ["老師", "教書"],
    "粵曲": ["粵曲", "大戲"], "大戲": ["大戲", "粵曲"], "膝頭": ["膝頭"], "腳": ["膝頭", "行得慢"],
    "住邊": ["住"], "探我": ["探"], "嚟探": ["探"],
}

# Function words removed before building search terms (multi-character first).
STOP_MULTI = ("係唔係", "記唔記得", "知唔知", "你知唔知", "話俾我知", "話我知", "記得", "幾時", "點樣", "乜嘢", "咩嘢",
              "係咪", "有冇", "同你", "講咗", "講過", "話過", "傾過", "傾咗", "我哋", "你哋", "佢哋", "而家", "阿寶",
              "王婆婆", "婆婆", "邊個", "邊度", "幾點", "咩名", "叫咩")
STOP_CHARS = set("我你佢咗咩嘅呀啊啦喇嗎嘛吖㗎喎囉咯噃係唔冇有同講話問知想要就都又仲會嚟噉咁呢嗰個啲乜的了吗是和吧過")


class MemoryRejected(ValueError):
    """A write was refused. `reason`: pii | empty | too_long."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


# ---------------------------------------------------------------- tokens
_CJK = re.compile(r"[　-鿿豈-﫿＀-￯]")


def estimate_tokens(text: str) -> int:
    """Conservative token estimate: one per CJK character, one per 4 other characters."""
    cjk = len(_CJK.findall(text or ""))
    return cjk + (len(text or "") - cjk + 3) // 4


# ---------------------------------------------------------------- facts
def _norm_category(category: str | None) -> str:
    c = (category or "").strip().lower()
    return c if c in CATEGORIES else "other"


def _check_text(text: str, max_len: int = 200) -> str:
    t = fts.clean(text)
    if not t:
        raise MemoryRejected("empty")
    if len(t) > max_len:
        raise MemoryRejected("too_long")
    kinds = find_pii(t)
    if kinds:
        raise MemoryRejected("pii", ",".join(kinds))
    return t


def add_fact(db: Database, clock: Clock, fact: str, category: str, *, importance: int = 3, status: str = "active",
             source: str = "voice", confidence: float | None = None, supersedes: str | None = None) -> str:
    """Insert a fact (PII-checked). An identical active/pending fact is not duplicated: its id is returned."""
    text = _check_text(fact)
    cat = _norm_category(category)
    if status not in ("active", "pending_review"):
        raise ValueError(f"bad status {status}")
    dup = db.one("SELECT id FROM facts WHERE fact=? AND status IN ('active','pending_review')", (text,))
    if dup:
        return dup["id"]
    fid = new_id("fact")
    db.execute("INSERT INTO facts(id,fact,category,importance,status,source,confidence,supersedes,created_at) "
               "VALUES (?,?,?,?,?,?,?,?,?)",
               (fid, text, cat, max(1, min(5, int(importance))), status, source, confidence, supersedes, clock.iso()))
    return fid


def remember(db: Database, clock: Clock, fact: str, category: str, source: str = "voice") -> str:
    """The `remember` tool / carer `/fact`. Family and health facts said by voice wait for the family's review
    (§7.2: one confused utterance must not overwrite 「阿珍係我女兒」). Raises MemoryRejected (e.g. PII)."""
    cat = _norm_category(category)
    status = "pending_review" if (cat in REVIEW_CATEGORIES and source not in TRUSTED_SOURCES) else "active"
    return add_fact(db, clock, fact, cat, status=status, source=source)


def supersede_fact(db: Database, clock: Clock, old_id: str, new_fact: str, category: str | None = None, *,
                   source: str = "carer", pending: bool = False, confidence: float | None = None) -> str:
    """Replace a fact. With pending=True the new fact waits for review and the old one stays active until
    confirm_fact; otherwise the old fact is marked superseded now."""
    old = db.one("SELECT * FROM facts WHERE id=?", (old_id,))
    if old is None:
        raise KeyError(old_id)
    cat = _norm_category(category or old["category"])
    if pending:
        return add_fact(db, clock, new_fact, cat, importance=old["importance"], status="pending_review",
                        source=source, confidence=confidence, supersedes=old_id)
    with db.transaction():
        nid = add_fact(db, clock, new_fact, cat, importance=old["importance"], source=source, confidence=confidence,
                       supersedes=old_id)
        if nid != old_id:
            db.execute("UPDATE facts SET status='superseded' WHERE id=?", (old_id,))
    return nid


def pending_review_facts(db: Database) -> list[dict[str, Any]]:
    """Facts waiting for the family's confirmation (shown in the digest with a [確認資料] button)."""
    rows = db.query("SELECT f.id, f.fact, f.category, f.confidence, f.source, f.created_at, f.supersedes, "
                    "o.fact AS supersedes_fact FROM facts f LEFT JOIN facts o ON o.id = f.supersedes "
                    "WHERE f.status='pending_review' ORDER BY f.created_at")
    return rows


def confirm_fact(db: Database, fact_id: str, accept: bool = True) -> bool:
    """Accept (→ active; the fact it supersedes → superseded) or reject (deleted: we keep only what's needed)."""
    f = db.one("SELECT * FROM facts WHERE id=? AND status='pending_review'", (fact_id,))
    if f is None:
        return False
    with db.transaction():
        if accept:
            db.execute("UPDATE facts SET status='active' WHERE id=?", (fact_id,))
            if f["supersedes"]:
                db.execute("UPDATE facts SET status='superseded' WHERE id=? AND status='active'", (f["supersedes"],))
        else:
            db.execute("DELETE FROM facts WHERE id=?", (fact_id,))
    return True


def active_facts(db: Database, limit_tokens: int = 800) -> list[dict[str, Any]]:
    """Active facts by importance (then newest), as many as fit in `limit_tokens` (§5.2 profile block)."""
    out, used = [], 0
    for r in db.query("SELECT id, fact, category, importance FROM facts WHERE status='active' "
                      "ORDER BY importance DESC, created_at DESC, rowid DESC"):
        cost = estimate_tokens(r["fact"]) + 2
        if used + cost > limit_tokens:
            continue           # a shorter, less important fact may still fit
        out.append(r)
        used += cost
    return out


# ---------------------------------------------------------------- episodes
def _session_date(opened_at: str) -> str:
    return (opened_at or "")[:10]


def _episode(r: dict[str, Any]) -> dict[str, Any]:
    lines = loads(r.get("summary"), []) or []
    return {"session_id": r["id"], "date": _session_date(r["opened_at"]),
            "summary": r.get("summary_text") or "".join(lines), "lines": lines, "mood": r.get("mood"),
            "topics": loads(r.get("topics"), []) or []}


def recent_episodes(db: Database, clock: Clock, n: int = 3) -> list[dict[str, Any]]:
    """The last n summarised sessions up to now (§5.2 recent episodic block)."""
    rows = db.query("SELECT * FROM sessions WHERE summary_text != '' ORDER BY opened_at DESC LIMIT ?", (n * 3,))
    now = clock.now()
    out = []
    for r in rows:
        try:
            if datetime.fromisoformat(r["opened_at"]) > now:
                continue
        except ValueError:
            pass
        out.append(_episode(r))
        if len(out) >= n:
            break
    return out


# ---------------------------------------------------------------- recall
def _strip(text: str, words: list[str] | tuple[str, ...]) -> str:
    for w in sorted(words, key=len, reverse=True):
        text = text.replace(w, " ")
    return text


def _query_terms(query: str) -> tuple[list[str], list[str], list[str]]:
    """(trigrams for FTS, short/bigram terms for LIKE, expansion words) from a free-text question."""
    q = fts.clean(query)
    expansions: list[str] = []
    for k, vs in EXPANSIONS.items():
        if k in q:
            expansions += vs
    q = _strip(q, list(DAY_WORDS) + list(RANGE_WORDS) + list(LAST_WORDS) + list(STOP_MULTI))
    q = "".join(" " if ch in STOP_CHARS else ch for ch in q)
    runs = fts.runs(q)
    total = sum(len(r) for r in runs)
    tri: list[str] = []
    like: list[str] = []
    for r in runs:
        if len(r) >= 3:
            tri += fts.ngrams(r, 3)
            like += fts.ngrams(r, 2)
        elif len(r) == 2 or total <= 2:
            like.append(r)
    for e in expansions:
        (tri if len(e) >= 3 else like).append(e)
    return list(dict.fromkeys(tri)), list(dict.fromkeys(like)), expansions


def _score(text: str, terms: list[str]) -> int:
    return sum(len(t) for t in terms if t and t in text)


def _day_targets(query: str, today: date) -> tuple[set[str], bool]:
    """Dates the question refers to (琴日 → yesterday …) and whether it asks about 'the last time'."""
    q = fts.clean(query)
    dates: set[str] = set()
    rest = q
    for w in sorted(DAY_WORDS, key=len, reverse=True):
        if w in rest:
            dates.add((today - timedelta(days=DAY_WORDS[w])).isoformat())
            rest = rest.replace(w, " ")
    for w, n in RANGE_WORDS.items():
        if w in rest:
            dates |= {(today - timedelta(days=i)).isoformat() for i in range(1, n + 1)}
    return dates, any(w in q for w in LAST_WORDS)


def recall(db: Database, query: str, limit: int = 5, *, clock: Clock | None = None) -> dict[str, list[dict[str, Any]]]:
    """Search active facts and session summaries (the `recall` tool, CONTRACTS §6)."""
    clock = clock or get_clock()
    tri, like, _ = _query_terms(query or "")
    all_terms = tri + like
    out: dict[str, list[dict[str, Any]]] = {"facts": [], "episodes": []}

    # facts: FTS candidates (≥3-char terms) ∪ LIKE candidates (1–2-char terms), scored by matched characters
    cand: dict[str, dict[str, Any]] = {}
    if all_terms:
        for r in fts.fts_search(db, "facts_fts", "facts", tri, where="b.status='active'",
                                columns="b.id, b.fact, b.category, b.importance"):
            cand[r["id"]] = r
        for r in fts.like_search(db, "facts", "fact", like, where="b.status='active'",
                                 columns="b.id, b.fact, b.category, b.importance"):
            cand.setdefault(r["id"], r)
    facts = sorted(cand.values(), key=lambda r: (-_score(r["fact"], all_terms), -r["importance"]))
    out["facts"] = [{"id": r["id"], "fact": r["fact"], "category": r["category"]}
                    for r in facts if _score(r["fact"], all_terms) > 0][:limit]

    # episodes: sessions of the dates named (琴日 …) first, then text matches, newest first on ties
    dates, last = _day_targets(query or "", clock.today())
    eps: dict[str, tuple[int, dict[str, Any]]] = {}
    now_iso = clock.now().isoformat()
    for d in sorted(dates):
        for r in db.query("SELECT * FROM sessions WHERE summary_text != '' AND substr(opened_at,1,10)=? "
                          "ORDER BY opened_at DESC", (d,)):
            eps[r["id"]] = (1000, r)
    if last:
        for r in db.query("SELECT * FROM sessions WHERE summary_text != '' AND opened_at <= ? "
                          "ORDER BY opened_at DESC LIMIT 1", (now_iso,)):
            eps.setdefault(r["id"], (900, r))
    if all_terms:
        rows = fts.fts_search(db, "sessions_fts", "sessions", tri, where="b.summary_text != ''")
        rows += fts.like_search(db, "sessions", "summary_text", like, where="b.summary_text != ''")
        for r in rows:
            s = _score(r["summary_text"], all_terms)
            if s > 0:
                prev = eps.get(r["id"], (0, r))[0]
                eps[r["id"]] = (prev + s, r)
    ranked = sorted(eps.values(), key=lambda x: (x[0], x[1]["opened_at"]), reverse=True)
    out["episodes"] = [{k: e[k] for k in ("session_id", "date", "summary")} for e in
                       (_episode(r) for _, r in ranked[:limit])]
    return out


# ---------------------------------------------------------------- context blocks (§5.2)
def profile_block(db: Database, limit_tokens: int = 800) -> str:
    facts = active_facts(db, limit_tokens)
    return "\n".join(f"- {f['fact']}" for f in facts)


def episodic_block(db: Database, clock: Clock, n: int = 3, limit_tokens: int = 500) -> str:
    today = clock.today()
    lines, used = [], 0
    for e in recent_episodes(db, clock, n):
        try:
            days = (today - date.fromisoformat(e["date"])).days
        except ValueError:
            days = -1
        when = {0: "今日", 1: "琴日", 2: "前日"}.get(days, e["date"])
        line = f"- {when}：{e['summary']}"
        cost = estimate_tokens(line)
        if used + cost > limit_tokens:
            break
        lines.append(line)
        used += cost
    return "\n".join(lines)
