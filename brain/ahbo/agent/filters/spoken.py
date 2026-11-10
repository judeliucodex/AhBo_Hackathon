"""Spoken-output filter (§5.6, INV-10). Runs on reply text before TTS / captions.

1. strip markdown, emoji, URLs; 2. digits/times → spoken Cantonese; 3. Mandarin-drift detector + conservative
replacement map (logged); 4. length guard: at most 2 sentences; 5. sentence splitting for TTS chunking.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ahbo.core.numwords import digits_to_words

URL_RE = re.compile(r"https?://\S+|www\.\S+")
MD_RE = re.compile(r"[*_`#>\[\]]|^\s*[-•]\s+", re.M)
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
SENT_END = "。！？"
SHORT_TAG_CHARS = 8   # a sentence this short is a tag, not a sentence, for the 2-sentence guard
SPLIT_RE = re.compile(r"(?<=[。！？；，])")
MANDARIN_MARKERS = ("的", "是", "在", "了", "他們", "她們", "沒有", "這", "那裡", "什麼", "怎麼")
CANTONESE_MARKERS = ("嘅", "咗", "喺", "唔", "佢", "啲", "冇", "嚟", "呀", "啦", "喎", "係", "㗎", "嗰", "乜", "咩", "點解")
DRIFT_MAP = (("沒有", "冇"), ("他們", "佢哋"), ("她們", "佢哋"), ("什麼", "咩"), ("怎麼", "點樣"),
             ("這個", "呢個"), ("那個", "嗰個"), ("是", "係"), ("在", "喺"), ("的", "嘅"))


@dataclass
class FilterResult:
    text: str
    flags: list[str] = field(default_factory=list)


def sentences(text: str) -> list[str]:
    out, cur = [], ""
    for ch in text:
        cur += ch
        if ch in SENT_END:
            out.append(cur)
            cur = ""
    if cur.strip():
        out.append(cur)
    return out


def chunks(text: str) -> list[str]:
    """TTS chunks: split on 。！？；，"""
    return [c for c in SPLIT_RE.split(text) if c.strip()]


def is_mandarin_drift(s: str) -> bool:
    return any(m in s for m in MANDARIN_MARKERS) and not any(c in s for c in CANTONESE_MARKERS)


def spoken_filter(text: str, *, max_sentences: int = 2, fix_drift: bool = True) -> FilterResult:
    flags: list[str] = []
    t = URL_RE.sub("", text or "")
    t = MD_RE.sub("", t)
    t2 = EMOJI_RE.sub("", t)
    if t2 != t:
        flags.append("emoji")
    t = t2
    if re.search(r"\d", t):
        flags.append("digits_spoken")
        t = digits_to_words(t)
    t = re.sub(r"\s+", "", t)
    sents = sentences(t)
    fixed = []
    for s in sents:
        if is_mandarin_drift(s):
            flags.append("mandarin_drift")
            if fix_drift:
                for a, b in DRIFT_MAP:
                    s = s.replace(a, b)
        fixed.append(s)
    kept, long_count = [], 0
    for s in fixed:
        is_tag = len(s) <= SHORT_TAG_CHARS          # 「有呀！」「想唔想去？」「到時我會提你㗎。」 (BUILD_LOG D-10)
        if not is_tag:
            if long_count >= max_sentences:
                flags.append("too_long")
                break
            long_count += 1
        kept.append(s)
    fixed = kept
    return FilterResult("".join(fixed).strip(), flags)
