"""speech.segments + speech.fakes (§10.3, §26.3): 0.19 s per character, UTF-16 offsets, monotonic segments."""
from __future__ import annotations

import itertools
from pathlib import Path

from ahbo.speech.audio import probe
from ahbo.speech.fakes import FakeTTS
from ahbo.speech.segments import SEC_PER_CHAR, estimate_segments, u16_offsets, u16len

LINES = [
    "哎呀！我感應到你好似跌低咗！",
    "王婆婆，如果你冇跌親，請講「我冇事」。",
    "王婆婆，夠鐘食藥喇：血壓藥、糖尿藥，同埋薄血丸。",
    "你好，呢度係阿寶。王婆婆喺屋企懷疑跌倒，冇回應。地址：九龍觀塘秀茂坪示範邨 12 樓 1203 室（示範地址）。請即刻跟進。",
    "Call 12345 試吓。",          # Latin run + digits
    "OK",                          # ASCII only
    "一家👨‍👩‍👧‍👦四口",           # non-BMP (UTF-16 surrogate pairs) — render safety only
]


def _check_shape(text: str, duration: float, segs: list[list[float]]) -> None:
    total = u16len(text)
    assert duration == round(SEC_PER_CHAR * len(text), 3)
    assert segs, "no segments"
    prev_end_t = -1.0
    prev_end_c = -1
    for a, b, s, e in segs:
        assert 0 <= a < b <= total, (a, b, total)
        assert a >= prev_end_c, "char offsets go backwards"
        assert s < e, (s, e)
        assert s >= prev_end_t - 1e-9, "time overlap"
        prev_end_t, prev_end_c = e, b
    last = segs[-1][1]
    assert last == total, f"last endChar {last} != UTF-16 length {total}"


def test_u16_offsets_count_surrogates_as_two() -> None:
    assert u16len("你好") == 2
    assert u16len("👨") == 2          # non-BMP: 2 UTF-16 code units
    offs = u16_offsets("a👨b")       # a(0) 👨(1..3) b(3), total 4 — the 👨 counts 2 units
    assert offs == [0, 1, 3, 4]


def test_estimate_segments_shape_for_fixed_lines() -> None:
    for text in LINES:
        if any(ord(c) > 0xFFFF for c in text):
            continue                  # shape checks assume BMP-only text
        duration, segs = estimate_segments(text)
        _check_shape(text, duration, segs)


def test_punctuation_is_a_pause_not_a_segment() -> None:
    text = "好啦，冇事就好。"
    duration, segs = estimate_segments(text)
    # 3 spoken units (好啦 / 冇事就好), 2 punctuation pauses
    assert duration == round(0.19 * len(text), 3)
    times = [(s, e) for _, _, s, e in segs]
    for (s1, e1), (s2, e2) in itertools.pairwise(times):
        assert e2 > s2
        assert s2 >= e1                       # the pause keeps the clock moving
    # a full stop at the end advances the clock: sum of segment durations + pauses == duration
    spoken = sum(e - s for s, e in times)
    assert duration > spoken                  # pauses are the difference


def test_opening_quote_revealed_with_following_unit() -> None:
    text = "請講「我冇事」。"
    _, segs = estimate_segments(text)
    # 「 (UTF-16 offset 2) is revealed together with 我 (the unit after it), never as its own segment;
    # the closing 」 (offset 6) and the full stop are revealed with 事 (the last unit).
    assert not any(a == 3 and b == 4 for a, b, *_ in segs)      # no segment for 「 alone
    assert any(a == 2 and b > 3 for a, b, *_ in segs)           # 「 rides with 我…
    assert segs[-1][1] == u16len(text)                          # …and the tail rides with 事


def test_fake_tts_render_writes_silent_aac(tmp_path: Path) -> None:
    tts = FakeTTS()
    text = LINES[0]
    out = tmp_path / "F1.m4a"
    duration, segs = tts.render(text, out)
    est_dur, est_segs = tts.estimate(text)
    assert duration == est_dur and segs == est_segs
    assert out.is_file() and out.stat().st_size > 0
    info = probe(out)
    assert info["codec"] == "aac"
    assert info["sample_rate"] == 24_000
    assert info["channels"] == 1
    assert abs(info["duration"] - duration) < 0.1   # container padding is small


def test_fake_tts_duration_matches_019_per_char(tmp_path: Path) -> None:
    tts = FakeTTS(sec_per_char=0.19)
    for text in LINES[:4]:
        out = tmp_path / f"{abs(hash(text))}.m4a"
        tts.render(text, out)
        info = probe(out)
        assert abs(info["duration"] - 0.19 * len(text)) < 0.1, text
