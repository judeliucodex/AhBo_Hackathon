"""Spoken filter table (§5.6, INV-10, D-10) + eval defect (d)."""
from __future__ import annotations

import pytest

from ahbo.agent.filters.spoken import StreamFilter, chunks, spoken_filter, to_traditional

TABLE = [
    # (input, expected output, flags that must be present)
    ("今日啲藥都食晒喇，好叻呀！", "今日啲藥都食晒喇，好叻呀！", []),                         # defect d: 晒 stays
    ("試吓搵吓郁吓，食晒佢。", "試吓搵吓郁吓，食晒佢。", []),
    ("你下一次食药系15:00，有4粒。", "你下一次食藥系下晝三點，有四粒。", ["simplified", "digits_spoken"]),
    ("**好呀**！睇吓呢度：https://example.com/x 😊", "好呀！睇吓呢度：", ["markdown", "url", "emoji"]),
    ("- 血壓藥\n- 糖尿藥", "血壓藥糖尿藥", ["markdown"]),
    ("你沒有吃藥。", "你冇食藥。", ["mandarin_drift"]),
    ("我們現在去吃飯吧。", None, ["mandarin_drift"]),
    ("OK，我幫你搵阿珍。", "，我幫你搵阿珍。".lstrip("，"), ["english"]),
    ("十一月20日去覆診", "十一月二十號去覆診", ["digits_spoken"]),
    ("溫度係23°C，濕度80%", "溫度係二十三度，濕度百分之八十", ["digits_spoken"]),
    ("Amlodipine 5mg", "五毫克", ["english", "digits_spoken"]),
    ("[阿寶系統｜家人｜唔係王婆婆講嘅] 阿珍話記得食飯。", "阿珍話記得食飯。", []),
    ("的士嚟咗喇。", "的士嚟咗喇。", []),                                                     # whitelisted word
]


@pytest.mark.parametrize("raw,want,flags", TABLE)
def test_filter_table(raw, want, flags):
    r = spoken_filter(raw)
    if want is not None:
        assert r.text == want, r
    for f in flags:
        assert f in r.flags, (f, r.flags)
    assert not any(c.isdigit() for c in r.text)


def test_length_guard_counts_long_sentences_only():
    # D-10: tags (≤ 8 chars) don't count
    s2 = "附近長者中心下晝三點有粵曲班，可以識多啲朋友。想唔想去？我幫你報名吖。"
    assert spoken_filter(s2).text == s2
    long3 = "十點半你要去覆診，記住帶覆診卡呀。九點半喺樓下搭巴士就得喇。之後十二點半同阿珍飲茶，佢會嚟接你。"
    r = spoken_filter(long3)
    assert "too_long" in r.flags and r.text.count("。") == 2
    assert "too_long" in r.quality


def test_traditional_shielding_keeps_cantonese_chars():
    assert to_traditional("晒吓搵郁") == "晒吓搵郁"
    assert to_traditional("这个药") == "這個藥"


def test_stream_filter_cuts_after_two_sentences():
    f = StreamFilter()
    out = []
    for piece in ["十點半你要去聯合醫院覆診，", "記住帶埋覆診卡呀。九點半喺樓下", "搭巴士就得喇。之後十二點半同阿珍飲茶，佢會嚟接你。"]:
        out += f.push(piece)
        if f.stopped:
            break
    assert f.stopped and len(out) == 2 and "too_long" in f.quality


def test_chunks_split_on_chinese_punctuation():
    assert chunks("王婆婆，你好呀。我係阿寶！") == ["王婆婆，", "你好呀。", "我係阿寶！"]
