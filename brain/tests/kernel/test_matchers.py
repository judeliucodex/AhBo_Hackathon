"""OK/help matcher and red-flag matcher (§4.3.1, §4.3.4) — deterministic, INV-2."""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from ahbo.core.config import load_config
from ahbo.kernel.matchers import match_ok, match_redflag, normalise, raise_level

CFG = load_config(fresh=True)
HELP = [str(x) for x in CFG.ok_phrases["help"]]
OK = [str(x) for x in CFG.ok_phrases["ok"]]


def test_ok_phrases_and_variants():
    for t in ("我冇事", "冇事！", "我 無 事。", "OK", "ok", "Ok啦", "ｏｋ", "冇問題", "我好好"):
        assert match_ok(t, CFG) == "ok", t
    assert match_ok("我无事", CFG) == "ok"          # simplified → traditional


def test_question_form_is_not_ok():
    for t in ("有冇事㗎？", "你有冇事呀", "有無事"):
        assert match_ok(t, CFG) is None, t


def test_help_wins_over_ok():
    assert match_ok("我冇事……哎呀好痛呀", CFG) == "help"
    assert match_ok("冇事，不過起唔到身", CFG) == "help"
    assert match_ok("救命！", CFG) == "help"
    assert match_ok("帮我", CFG) == "help"          # simplified help phrase
    assert match_ok("头晕", CFG) == "help"


def test_nothing_matched():
    assert match_ok("", CFG) is None
    assert match_ok("今日天氣幾好", CFG) is None


def test_normalise():
    assert normalise(" 我 冇事！ OK ") == "我冇事ok"


@settings(max_examples=300, deadline=None)
@given(a=st.text(max_size=12), h=st.sampled_from(HELP), b=st.text(max_size=12), ok=st.sampled_from(OK))
def test_no_help_phrase_ever_yields_ok(a, h, b, ok):
    assert match_ok(a + h + b, CFG) == "help"
    assert match_ok(ok + a + h + b, CFG) == "help"
    assert match_ok(a + h + b + ok, CFG) == "help"


def test_redflags():
    assert match_redflag("阿寶，我胸口痛呀", CFG)[:2] == ("emergency", "health")
    assert match_redflag("我好似唞唔到氣", CFG)[0] == "emergency"
    assert match_redflag("我頭好暈呀", CFG)[:2] == ("urgent", "health")
    assert match_redflag("有人話個孫被捕要過數", CFG)[:2] == ("urgent", "scam")
    assert match_redflag("今日好開心", CFG) is None
    # highest wins
    assert match_redflag("頭好暈，胸口痛", CFG)[0] == "emergency"


def test_raise_level_never_lowers():
    assert raise_level("urgent", "info") == "urgent"
    assert raise_level("info", "emergency") == "emergency"
    assert raise_level(None, None) == "info"


# ---------------------------------------------------------------- STT spellings (ElevenLabs writes Simplified/formal)
def test_simplified_and_formal_ok_forms():
    for t in ("我没事", "我沒事", "沒事", "没事", "我无事", "我没有事", "没问题"):
        assert match_ok(t, CFG) == "ok", t


def test_simplified_and_formal_help_forms():
    for t in ("救命", "好痛", "起唔到身", "起不了身", "我起不来", "跌倒了", "帮我", "幫我", "我摔倒了",
              "叫救护车", "我没事，不过起不了身"):
        assert match_ok(t, CFG) == "help", t


def test_stt_traps_are_not_ok():
    for t in ("冇食", "我冇食", "有冇事", "你有没有事呀", "有沒有事", "不是没事", "点会没事"):
        assert match_ok(t, CFG) is None, t
