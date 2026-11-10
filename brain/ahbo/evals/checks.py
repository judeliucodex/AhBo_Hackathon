"""INV-10 checks for one spoken line (§2, §5.6, BUILD_LOG D-10). Independent of the spoken filter on purpose: the
eval must not grade the filter with the filter's own code.

A line passes when it has no digits, emoji, markdown, URLs or Latin letters (English), no Simplified characters
(OpenCC s2hk), no Mandarin drift, and at most 2 sentences longer than 8 characters (shorter ones are tags, D-10).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

DIGIT_RE = re.compile(r"[0-9０-９]")
URL_RE = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|org|net|hk|io|gov)\b", re.I)
MARKDOWN_RE = re.compile(r"\*\*|__|`|^\s*#|^\s*[-*•]\s|\[[^\]]*\]\(|^\s*>|\|\s*---", re.M)
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF\U00002B00-\U00002BFF"
                      "️‍←-⇿⌀-⏿]")
LATIN_RE = re.compile(r"[A-Za-zＡ-Ｚａ-ｚ]{2,}")
SENT_END = "。！？!?"
SHORT_TAG_CHARS = 8          # D-10: a sentence of ≤ 8 characters (with its mark) is a tag and does not count
MAX_SENTENCES = 2

# §5.6 markers: drift = Mandarin markers without any Cantonese marker in the same sentence.
MANDARIN_MARKERS = ("的", "是", "在", "了", "他們", "她們", "沒有", "這", "那裡", "什麼", "怎麼")
CANTONESE_MARKERS = ("嘅", "咗", "喺", "唔", "佢", "啲", "冇", "嚟", "呀", "啦", "喎", "係", "㗎", "嗰", "乜", "咩",
                     "點解", "吖", "嘢", "睇", "俾", "畀", "咁", "喇", "囉", "吓", "哋", "嘞", "而家", "邊個", "幾時")
# Words a Cantonese speaker would not say; drift even next to Cantonese markers (written/Mandarin forms).
HARD_MANDARIN = ("沒有", "他們", "她們", "什麼", "怎麼", "這個", "那個", "這裡", "那裡", "我們", "你們", "不要",
                 "不是", "喜歡", "東西", "現在", "吃", "看看", "知道了", "好的")
# Characters OpenCC rewrites although they are standard HK colloquial Traditional.
SIMPLIFIED_ALLOW = set("吓晒搵郁")   # s2hk rewrites these HK colloquial characters (吓→嚇 晒→曬 搵→揾 郁→鬱)


@dataclass
class LineCheck:
    text: str
    violations: list[str] = field(default_factory=list)
    simplified: list[str] = field(default_factory=list)
    sentences: int = 0

    @property
    def ok(self) -> bool:
        return not self.violations

    @property
    def drift(self) -> bool:
        return "mandarin_drift" in self.violations


@lru_cache(maxsize=1)
def _s2hk():
    import opencc   # opencc-python-reimplemented 0.1.7 (configs: s2hk, s2t, t2s, …)
    return opencc.OpenCC("s2hk")


def simplified_chars(text: str) -> list[str]:
    """Characters OpenCC's s2hk would rewrite, i.e. Simplified forms (minus known HK colloquial characters)."""
    conv = _s2hk().convert(text)
    if len(conv) != len(text):        # phrase conversion changed the length: fall back to per character
        return [c for c in text if c not in SIMPLIFIED_ALLOW and _s2hk().convert(c) != c]
    return [a for a, b in zip(text, conv) if a != b and a not in SIMPLIFIED_ALLOW]


def split_sentences(text: str) -> list[str]:
    out, cur = [], ""
    for ch in text:
        cur += ch
        if ch in SENT_END:
            out.append(cur)
            cur = ""
    if cur.strip():
        out.append(cur)
    return out


def long_sentences(text: str) -> int:
    return sum(1 for s in split_sentences(text) if len(s.strip()) > SHORT_TAG_CHARS)


def mandarin_drift(text: str) -> bool:
    for s in split_sentences(text):
        if any(w in s for w in HARD_MANDARIN):
            return True
        if any(m in s for m in MANDARIN_MARKERS) and not any(c in s for c in CANTONESE_MARKERS):
            return True
    return False


def check_line(text: str, *, max_sentences: int = MAX_SENTENCES) -> LineCheck:
    r = LineCheck(text=text)
    if not text or not text.strip():
        return r
    if DIGIT_RE.search(text):
        r.violations.append("digits")
    if EMOJI_RE.search(text):
        r.violations.append("emoji")
    if MARKDOWN_RE.search(text):
        r.violations.append("markdown")
    if URL_RE.search(text):
        r.violations.append("url")
    if LATIN_RE.search(text):
        r.violations.append("english")
    r.simplified = simplified_chars(text)
    if r.simplified:
        r.violations.append("simplified")
    if mandarin_drift(text):
        r.violations.append("mandarin_drift")
    r.sentences = long_sentences(text)
    if r.sentences > max_sentences:
        r.violations.append("too_long")
    return r
