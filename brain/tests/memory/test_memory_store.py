from __future__ import annotations

import pytest

from ahbo.core.jsonutil import dumps
from ahbo.memory import store


def facts(r):
    return [f["fact"] for f in r["facts"]]


@pytest.mark.parametrize("q,expect", [
    ("任劍輝", "鍾意聽粵曲，最鍾意任劍輝"),                    # ≥3 chars: FTS trigram
    ("粵曲", "鍾意聽粵曲，最鍾意任劍輝"),                       # 2 chars: LIKE fallback
    ("孫", "孫仔叫阿明，讀小學三年級"),                         # 1 char: LIKE fallback
    ("阿珍幾時嚟？", "女兒叫阿珍，住喺將軍澳，星期日會嚟探"),       # stopwords stripped, bigram
    ("我個女叫咩名？", "女兒叫阿珍，住喺將軍澳，星期日會嚟探"),     # expansion 個女 → 女兒
    ("我以前做咩工？", "以前係小學中文老師，教咗三十年書"),
    ("我鍾意食咩？", "鍾意飲茶，最愛蝦餃"),
])
def test_recall_facts(db, q, expect):
    r = store.recall(db, q)
    assert expect in facts(r)
    assert set(r) == {"facts", "episodes"}
    assert all(set(f) == {"id", "fact", "category"} for f in r["facts"])


def test_recall_yesterday_by_date(db, clock):
    r = store.recall(db, "我琴日同你講咗咩？")
    assert r["episodes"], r
    ep = r["episodes"][0]
    assert ep["date"] == "2026-11-19" and "膝頭" in ep["summary"]
    assert set(ep) == {"session_id", "date", "summary"}
    r2 = store.recall(db, "前日傾咗咩")
    assert r2["episodes"][0]["date"] == "2026-11-18"
    r3 = store.recall(db, "上次我哋傾咩？")
    assert r3["episodes"][0]["date"] == "2026-11-19"


def test_recall_episode_text_match(db):
    r = store.recall(db, "帝女花")
    assert r["episodes"] and "帝女花" in r["episodes"][0]["summary"]


def test_recall_limit_and_empty(db):
    assert store.recall(db, "") == {"facts": [], "episodes": []}
    assert store.recall(db, "？？？") == {"facts": [], "episodes": []}
    assert len(store.recall(db, "鍾意", limit=1)["facts"]) == 1


def test_recall_ignores_inactive(db, clock):
    fid = store.add_fact(db, clock, "最鍾意食叉燒包", "preference")
    store.supersede_fact(db, clock, fid, "最鍾意食燒賣", "preference")
    got = facts(store.recall(db, "叉燒包"))
    assert "最鍾意食叉燒包" not in got
    assert "最鍾意食燒賣" in facts(store.recall(db, "燒賣"))


def test_remember_and_pii(db, clock):
    fid = store.remember(db, clock, "鍾意朝早去公園散步", "routine")
    row = db.one("SELECT * FROM facts WHERE id=?", (fid,))
    assert row["status"] == "active" and row["source"] == "voice"
    assert store.remember(db, clock, "鍾意朝早去公園散步", "routine") == fid     # no duplicate
    for bad in ("我身份證係A123456(7)", "我張卡4111 1111 1111 1111", "密碼係1234", "我電話係91234567"):
        with pytest.raises(store.MemoryRejected) as e:
            store.remember(db, clock, bad, "other")
        assert e.value.reason == "pii"
    with pytest.raises(store.MemoryRejected):
        store.remember(db, clock, "  ", "other")
    assert db.one("SELECT COUNT(*) n FROM facts WHERE fact LIKE '%4111%' OR fact LIKE '%A123456%'")["n"] == 0


def test_voice_family_fact_waits_for_review(db, clock):
    fid = store.remember(db, clock, "阿珍係我新抱", "family")
    assert db.one("SELECT status FROM facts WHERE id=?", (fid,))["status"] == "pending_review"
    tid = store.remember(db, clock, "阿明鍾意踢波", "family", source="telegram")
    assert db.one("SELECT status FROM facts WHERE id=?", (tid,))["status"] == "active"


def test_pending_and_confirm(db, clock):
    old = db.one("SELECT id FROM facts WHERE fact LIKE '女兒叫阿珍%'")["id"]
    new = store.supersede_fact(db, clock, old, "女兒叫阿珍，搬咗去沙田", pending=True, source="extractor")
    assert db.one("SELECT status FROM facts WHERE id=?", (old,))["status"] == "active"   # not overwritten yet
    pend = store.pending_review_facts(db)
    assert [p["id"] for p in pend] == [new] and pend[0]["supersedes_fact"].startswith("女兒叫阿珍")
    assert store.confirm_fact(db, new) is True
    assert db.one("SELECT status FROM facts WHERE id=?", (old,))["status"] == "superseded"
    assert db.one("SELECT status FROM facts WHERE id=?", (new,))["status"] == "active"
    assert store.confirm_fact(db, new) is False
    rej = store.remember(db, clock, "有糖尿病", "health")
    assert store.confirm_fact(db, rej, accept=False) is True
    assert db.one("SELECT id FROM facts WHERE id=?", (rej,)) is None


def test_active_facts_ranked_and_budgeted(db):
    all_ = store.active_facts(db, 10_000)
    imps = [f["importance"] for f in all_]
    assert imps == sorted(imps, reverse=True) and len(all_) == 6
    small = store.active_facts(db, 40)
    assert 0 < len(small) < 6
    assert sum(store.estimate_tokens(f["fact"]) + 2 for f in small) <= 40
    assert all(f["importance"] == 5 for f in small)
    assert store.active_facts(db, 0) == []
    assert "女兒叫阿珍" in store.profile_block(db, 800)


def test_recent_episodes(db, clock):
    eps = store.recent_episodes(db, clock, n=3)
    assert [e["date"] for e in eps] == ["2026-11-19", "2026-11-18"]
    assert eps[0]["lines"][0].startswith("佢話琴日膝頭") and eps[0]["mood"] == "calm"
    db.execute("INSERT INTO sessions(id,opened_at,summary,summary_text) VALUES (?,?,?,?)",
               ("sess_today", "2026-11-20T09:00:00+08:00", dumps(["今朝傾咗天氣。"]), "今朝傾咗天氣。"))
    db.execute("INSERT INTO sessions(id,opened_at,summary_text) VALUES (?,?,?)",
               ("sess_future", "2026-11-21T09:00:00+08:00", "未來"))
    eps = store.recent_episodes(db, clock, n=2)
    assert eps[0]["session_id"] == "sess_today" and len(eps) == 2
    block = store.episodic_block(db, clock)
    assert block.startswith("- 今日：今朝傾咗天氣。") and "琴日" in block
