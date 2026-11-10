"""Deterministic number → spoken Cantonese converter (§5.6, §23.6). Shared by the spoken filter, step labels,
cards and fixed lines. Pure functions; covered by tests/core/test_numwords.py.
"""
from __future__ import annotations

import re
from datetime import datetime

DIGITS = "零一二三四五六七八九"
COUNT_CLASSIFIERS = "個隻粒次間位張條件本杯碗日年歲號點分秒樓"


def int_words(n: int, counting: bool = False) -> str:
    """0..99999 in Cantonese. counting=True uses 兩 for a leading 2 (兩個, 兩隻, 兩點)."""
    if n < 0:
        return "負" + int_words(-n)
    if n < 10:
        return "兩" if (counting and n == 2) else DIGITS[n]
    if n < 20:
        return "十" + (DIGITS[n - 10] if n > 10 else "")
    if n < 100:
        t, u = divmod(n, 10)
        return DIGITS[t] + "十" + (DIGITS[u] if u else "")
    if n < 1000:
        h, r = divmod(n, 100)
        head = ("兩" if counting and h == 2 else DIGITS[h]) + "百"
        if r == 0:
            return head
        if r < 10:
            return head + "零" + DIGITS[r]
        return head + (("一" + int_words(r)) if r < 20 else int_words(r))
    if n < 10000:
        k, r = divmod(n, 1000)
        head = ("兩" if counting and k == 2 else DIGITS[k]) + "千"
        if r == 0:
            return head
        if r < 100:
            return head + "零" + (("一" + int_words(r)) if 10 <= r < 20 else int_words(r))
        return head + int_words(r)
    w, r = divmod(n, 10000)
    return int_words(w, counting) + "萬" + (("零" if r < 1000 else "") + int_words(r) if r else "")


def hour12(h: int) -> int:
    h = h % 24
    return 12 if h % 12 == 0 else h % 12


def period_words(h: int) -> str:
    if h < 5:
        return "凌晨"
    if h < 12:
        return "朝早"
    if h < 13:
        return "中午"
    if h < 18:
        return "下晝"
    return "夜晚"


def time_words(h: int, m: int = 0, *, period: bool = False, zheng: bool = False) -> str:
    """15:00 → 三點 (period=True: 下晝三點); 12:00 zheng=True → 十二點正; 9:30 → 九點半; 8:05 → 八點零五分."""
    head = int_words(hour12(h), counting=True) + "點"
    if m == 0:
        tail = "正" if zheng else ""
    elif m == 30:
        tail = "半"
    elif m < 10:
        tail = "零" + DIGITS[m] + "分"
    else:
        tail = int_words(m) + "分"
    return (period_words(h) if period else "") + head + tail


def hhmm_words(hhmm: str, **kw) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    return time_words(h, m, **kw)


def card_time(hhmm: str) -> str:
    """Card labels use 12-hour h:mm, as in the kit: 15:00 → 3:00, 09:30 → 9:30, 12:30 → 12:30."""
    h, m = (int(x) for x in hhmm.split(":"))
    return f"{hour12(h)}:{m:02d}"


def rel_words(minutes: float) -> str:
    """Minutes from now → 兩個鐘之後 / 半個鐘之後 / 大約一個半鐘之後 / 二十分鐘之後 / 而家."""
    m = int(round(minutes))
    if m <= 1:
        return "而家"
    if m < 60:
        if m == 30:
            return "半個鐘之後"
        m5 = max(5, int(round(m / 5.0)) * 5) if m > 10 else m
        return f"{int_words(m5)}分鐘之後"
    h, r = divmod(m, 60)
    if r == 0:
        return f"{int_words(h, counting=True)}個鐘之後"
    if r == 30:
        return f"{int_words(h, counting=True)}個半鐘之後"
    if r < 15:
        return f"大約{int_words(h, counting=True)}個鐘之後"
    if r < 45:
        return f"大約{int_words(h, counting=True)}個半鐘之後"
    return f"大約{int_words(h + 1, counting=True)}個鐘之後"


def day_part_short(dt: datetime, now: datetime) -> str:
    """For card `small` lines: 今朝 7:42 / 今日下晝 3:10 / 今晚 8:00 / 琴日 9:00."""
    hm = f"{hour12(dt.hour)}:{dt.minute:02d}"
    if dt.date() == now.date():
        if dt.hour < 12:
            return f"今朝 {hm}"
        if dt.hour < 18:
            return f"今日下晝 {hm}"
        return f"今晚 {hm}"
    if (now.date() - dt.date()).days == 1:
        return f"琴日 {hm}"
    return f"{dt.month}月{dt.day}日 {hm}"


_TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)(?!\d)")
_NUM_RE = re.compile(r"\d+")


def digits_to_words(text: str) -> str:
    """Replace times and integers in spoken text with Cantonese words (INV-10: no digits spoken)."""
    text = _TIME_RE.sub(lambda mt: time_words(int(mt.group(1)), int(mt.group(2)), period=True), text)

    def num(mt: re.Match) -> str:
        n = int(mt.group(0))
        nxt = text[mt.end(): mt.end() + 1]
        return int_words(n, counting=bool(nxt) and nxt in COUNT_CLASSIFIERS)

    return _NUM_RE.sub(num, text)
