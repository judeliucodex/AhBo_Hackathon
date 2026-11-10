"""INV-10: Cantonese only, short, speakable. Every `say_start` the Kernel sends in S1–S4 (+ S4-OK) and every fixed
line passes the eval checks (no digits/emoji/markdown/URLs/English, Traditional only, no Mandarin drift, ≤ 2
sentences per D-10); the spoken filter makes hostile model output speakable. Also unit-tests the checks."""
from __future__ import annotations

import pytest

from ahbo.agent.filters.spoken import spoken_filter
from ahbo.evals.checks import check_line
from inv_helpers import fall, need, until_state, until_ui


def assert_lines_ok(lines: list[str]) -> None:
    assert lines, "nothing was spoken"
    bad = {ln: check_line(ln).violations for ln in lines if not check_line(ln).ok}
    assert not bad, f"INV-10 violations: {bad}"


# ------------------------------------------------------------------ the checks themselves
@pytest.mark.parametrize("text,violation", [
    ("十二點食藥，有4隻藥。", "digits"),
    ("好呀😊", "emoji"),
    ("**記住**食藥呀", "markdown"),
    ("睇吓 https://example.com 啦", "url"),
    ("OK，我幫你搵阿珍。", "english"),
    ("这个药你要记得食呀", "simplified"),
    ("我們現在去吃飯吧。", "mandarin_drift"),
    ("你沒有吃藥。", "mandarin_drift"),
    ("十點半你要去覆診，記住帶覆診卡呀。九點半喺樓下搭巴士就得喇。之後十二點半同阿珍飲茶，佢會嚟接你。", "too_long"),
])
def test_check_line_catches(text, violation):
    assert violation in check_line(text).violations


@pytest.mark.parametrize("text", [
    "兩個鐘之後，十二點正。有血壓藥、糖尿藥、膽固醇藥，同埋薄血丸。到時我會提你㗎。",
    "附近長者中心下晝三點有粵曲班，可以識多啲朋友。想唔想去？我幫你報名吖。",
    "今日啲藥都食晒喇，好叻呀！",
    "我而家影張相睇吓先。",
    "唔好郁住，阿珍就嚟。",
])
def test_check_line_accepts_colloquial_cantonese(text):
    assert check_line(text).ok, check_line(text).violations


async def test_fixed_lines_pass(system):
    lines = {k: v for k, v in system.config.fixed_lines.items() if not k.endswith("_TEMPLATE")}
    lines["R_s_1200"] = system.config.fixed_lines["R_TEMPLATE"].format(med_names="血壓藥、糖尿藥、膽固醇藥同埋薄血丸")
    assert_lines_ok(list(lines.values()))


# ------------------------------------------------------------------ the scenarios
@pytest.mark.parametrize("anchor,lines", [
    ("09:00", ["阿寶，今日有冇人搵過我呀？"]),
    ("10:00", ["阿寶，我幾時食藥呀？"]),
    ("08:30", ["阿寶，我今日有咩安排呀？", "咁我下晝好悶喎……"]),
], ids=["S1", "S2", "S3"])
async def test_conversation_scenarios_speak_cleanly(system, anchor, lines):
    s = system
    s.clock.set_time(anchor)
    for ln in lines:
        await s.say(ln)
    await s.wait(6)
    assert_lines_ok(s.spoken())


@pytest.mark.parametrize("ok_branch", [False, True], ids=["S4", "S4ok"])
async def test_fall_scenarios_speak_cleanly(system, ok_branch):
    s = system
    need(s, "reducer:fall")
    s.clock.set_time("15:00")
    await fall(s)
    if ok_branch:
        assert await until_ui(s, "countdown")
        await s.say("我冇事", wait=False)
        assert await until_state(s, "RESOLVED_OK", 15)
    else:
        assert await until_state(s, "ESCALATING", 60)
    await s.wait(10)
    assert_lines_ok(s.spoken())


# ------------------------------------------------------------------ the spoken filter on hostile model output
@pytest.mark.parametrize("raw", [
    "你下一次食藥係15:00，有4粒。",
    "**好呀**！睇吓呢度：https://example.com/x 😊",
    "- 血壓藥\n- 糖尿藥",
    "好呀。我幫你諗吓。之後再話你知。仲有好多嘢想同你講。今日天氣好好。",
    "这个是简体字，你记得食药呀。",
])
def test_spoken_filter_output_is_speakable(raw):
    out = spoken_filter(raw).text
    v = set(check_line(out).violations) - {"mandarin_drift"}
    assert not v, f"{raw!r} → {out!r}: {v}"
