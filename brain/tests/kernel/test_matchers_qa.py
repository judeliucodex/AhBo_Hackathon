"""QA 21 D1 (negated OK must not resolve a fall) and QA 22 D1 (symptom phrasing variants reach the red-flag net)."""
import pytest

from ahbo.kernel.matchers import match_ok, match_redflag


@pytest.mark.parametrize("text", ["唔OK", "唔ok", "我唔係OK", "唔係ok", "not ok", "I am not ok", "冇事個頭",
                                  "我唔係好OK", "唔係冇問題", "唔太ok呀"])
def test_negated_ok_is_not_ok(text):
    assert match_ok(text) != "ok"


@pytest.mark.parametrize("text", ["我冇事", "OK呀", "我ok", "冇問題，我好好", "唔使擔心，我冇事", "唔係呀，我OK"])
def test_real_ok_still_ok(text):
    assert match_ok(text) == "ok"


@pytest.mark.parametrize("text", ["阿寶，我胸口好痛呀", "心口好痛呀", "我透大氣都透唔到呀", "心口好似好翳",
                                  "我唞氣唞唔切", "我胸口痛"])
def test_symptom_variants_are_emergency(text):
    hit = match_redflag(text)
    assert hit and hit[0] == "emergency"


@pytest.mark.parametrize("text", ["今日天氣好好", "我想食叉燒包", "阿明幾時嚟呀"])
def test_ordinary_talk_no_redflag(text):
    assert match_redflag(text) is None
