from __future__ import annotations

import pytest

from ahbo.memory import fts
from ahbo.memory.pii import REDACTED, find_pii, has_pii, redact

NASTY = ['"', '""', 'AND', 'OR', 'NOT', 'NEAR(a b)', '*', 'a*', '^start', 'fact:阿珍', '(阿珍', '阿珍)', "'; DROP TABLE facts;--",
         '\x00阿珍\x00', '{fact}:x', '-阿珍', '+', '%', '_', '\\', '阿"珍"叫', 'NEAR("阿珍" "女兒", 2)', '😀😀😀', 'a' * 5000]


def test_phrase_quotes_and_doubles():
    assert fts.phrase('阿"珍') == '"阿""珍"'
    assert fts.match_any(["阿珍", "將軍澳", "將軍澳"]) == '"將軍澳"'
    assert fts.match_any(["ab"]) is None


@pytest.mark.parametrize("q", NASTY)
def test_untrusted_text_never_breaks_match(db, q):
    terms = fts.runs(q) + [q]
    rows = fts.fts_search(db, "facts_fts", "facts", terms)
    assert isinstance(rows, list)
    assert isinstance(fts.like_search(db, "facts", "fact", terms), list)
    # direct MATCH with the escaped expression is valid syntax
    expr = fts.match_any([fts.clean(q)])
    if expr:
        db.query("SELECT rowid FROM facts_fts WHERE facts_fts MATCH ?", (expr,))


def test_like_escapes_wildcards(db, clock):
    from ahbo.memory import store
    store.add_fact(db, clock, "百分之百%鍾意", "preference")
    assert fts.like_pattern("50%_\\") == "%50\\%\\_\\\\%"
    hits = fts.like_search(db, "facts", "fact", ["%"])
    assert [h["fact"] for h in hits] == ["百分之百%鍾意"]
    assert fts.like_search(db, "facts", "fact", ["_"]) == []


@pytest.mark.parametrize("text,kind", [
    ("我身份證係A123456(7)", "hkid"), ("身份證號碼 AB987654（3）", "hkid"), ("z1234563", "hkid"),
    ("張卡號碼係 4111 1111 1111 1111", "number"), ("4111-1111-1111-1111", "number"),
    ("打俾我 9123 4567", "number"), ("+852 61234567", "number"), ("電話九一二三四五六七", "number"),
    ("我個密碼係abc123", "password"), ("password is hunter2", "password"), ("驗證碼係八八八", "password"),
    ("email me a@b.com", "email"),
])
def test_pii_detected(text, kind):
    assert kind in find_pii(text)
    assert has_pii(text)


@pytest.mark.parametrize("text", ["鍾意聽粵曲，最鍾意任劍輝", "教咗三十年書", "十二點食藥", "阿明讀小學三年級",
                                  "今日係十一月二十號", "2026年", "12:30 飲茶", "住喺12樓"])
def test_pii_clean(text):
    assert find_pii(text) == []


def test_redact():
    out = redact("我電話係91234567，鍾意飲茶")
    assert "91234567" not in out and REDACTED in out and "鍾意飲茶" in out
    out = redact("身份證A123456(7)。密碼係abc，好")
    assert "A123456" not in out and "abc" not in out and out.endswith("，好")
    assert redact("") == "" and redact("鍾意飲茶") == "鍾意飲茶"
