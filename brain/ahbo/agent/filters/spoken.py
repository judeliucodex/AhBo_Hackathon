"""Spoken-output filter (§5.6, INV-10). Runs on reply text before TTS / captions.

1. strip markdown, emoji, URLs, e-mail addresses and stray Latin words (med names, "OK");
2. Simplified → Traditional (OpenCC s2t) only when a Simplified-only character is present, shielding HK colloquial
   characters OpenCC would wrongly change (晒 吓 搵 郁 …, KEEP_CHARS); flagged `simplified` when something changed;
3. numbers → spoken Cantonese: dates (十一月二十號), years (二零二六年), decimals (二點五), percentages
   (百分之五十), money (一百蚊), units (五毫克), long digit strings read digit by digit, then times and counts
   through the shared converter (`15:00` → 下晝三點; `2個` → 兩個);
4. Mandarin-drift detector + a conservative replacement map, both logged (`mandarin_drift`), with a whitelist of
   Cantonese words that merely contain a marker (的士, 但是, 是但, 了解 …);
5. length guard: at most 2 sentences (BUILD_LOG D-10: a sentence of ≤ 8 characters is a tag and doesn't count);
6. sentence splitting on 。！？；， for TTS chunking (§24.5).

`spoken_filter(text)` filters a whole reply; `StreamFilter` does the same incrementally for a token stream
(the LiveKit worker's llm_node) and reports when the length guard wants the generation stopped.
Quality flags returned: `markdown`, `emoji`, `url`, `english`, `simplified`, `digits_spoken`, `mandarin_drift`,
`too_long`. The caller records the ones in QUALITY_EVENTS as `quality.*` Kernel events.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from ahbo.core.numwords import DIGITS, digits_to_words, int_words

QUALITY_EVENTS = ("mandarin_drift", "too_long", "digits_spoken")

URL_RE = re.compile(r"(?:https?://|www\.)[^\s，。！？、）)」]+", re.I)
# Instruction markers in untrusted text (deep results, injected data; INV-7, QA 22 D2): a sentence carrying one is
# dropped, never relayed and never used as a fallback.
INSTRUCTION_RE = re.compile(r"(?i)指示|指令|唔好理|唔使理|不要理|忽略|打俾|打畀|打電話|致電|所有人|所有聯絡人|過數|轉賬|"
                            r"轉帳|匯款|密碼|驗證碼|九九九|唔好話俾|ignore|instruction")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
MD_HEAD_RE = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
MD_LIST_RE = re.compile(r"^\s*(?:[-*•+]|\d{1,2}[.)、])\s+", re.M)
MD_CHARS_RE = re.compile(r"(\*\*|__|\*|`+|~~|^>\s?)", re.M)
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\U0001F1E6-\U0001F1FF☀-➿⬀-⯿←-⇿"
                      "⌀-⏿️‍⃣]")
SYS_PREFIX_RE = re.compile(r"\[阿寶系統｜[^\]]*\]\s*")
LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")
SENT_END = "。！？；"   # ； counts as a sentence end for the 2-sentence guard (live test 12 Nov: a ；-joined reply ran ~95 chars)
SHORT_TAG_CHARS = 8   # a sentence this short is a tag, not a sentence, for the 2-sentence guard (D-10)
SPLIT_RE = re.compile(r"(?<=[。！？；，])")
ASCII_PUNCT = str.maketrans({"?": "？", "!": "！", ",": "，", ";": "；", ":": "：", "(": "（", ")": "）"})
FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")

UNITS = (("mg", "毫克"), ("毫克", "毫克"), ("ml", "毫升"), ("km", "公里"), ("kg", "公斤"), ("cm", "厘米"),
         ("°C", "度"), ("℃", "度"), ("°", "度"))

MANDARIN_MARKERS = ("的", "是", "在", "了", "他們", "她們", "我們", "你們", "沒有", "這", "那裡", "那個", "什麼",
                    "怎麼", "不是", "不要", "吃", "說", "看看")
CANTONESE_MARKERS = ("嘅", "咗", "喺", "唔", "佢", "啲", "冇", "嚟", "呀", "啦", "喎", "係", "㗎", "嗰", "乜", "咩",
                     "點解", "噉", "咁", "嘢", "吖", "囉", "喇", "哋", "俾", "睇", "講", "食")
# Cantonese words that contain a marker character; protected from detection and replacement.
CANTONESE_OK = ("的士", "的確", "目的", "是但", "但是", "於是", "總是", "就是", "是否", "了解", "受唔了", "得了",
                "現在", "存在", "實在", "自在", "在意", "在乎", "這樣")
DRIFT_MAP = (("沒有", "冇"), ("他們", "佢哋"), ("她們", "佢哋"), ("我們", "我哋"), ("你們", "你哋"),
             ("什麼", "咩"), ("怎麼", "點樣"), ("這裡", "呢度"), ("那裡", "嗰度"), ("這個", "呢個"),
             ("那個", "嗰個"), ("不是", "唔係"), ("不要", "唔好"), ("吃", "食"), ("是", "係"), ("在", "喺"),
             ("的", "嘅"), ("這", "呢"))


@dataclass
class FilterResult:
    text: str
    flags: list[str] = field(default_factory=list)

    @property
    def quality(self) -> list[str]:
        return [f for f in dict.fromkeys(self.flags) if f in QUALITY_EVENTS]


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


def _protect(s: str) -> tuple[str, dict[str, str]]:
    keep: dict[str, str] = {}
    for i, w in enumerate(CANTONESE_OK):
        if w in s:
            tok = chr(0xE000 + i)        # private-use placeholder, one char
            keep[tok] = w
            s = s.replace(w, tok)
    return s, keep


def _restore(s: str, keep: dict[str, str]) -> str:
    for tok, w in keep.items():
        s = s.replace(tok, w)
    return s


def is_mandarin_drift(s: str) -> bool:
    p, _ = _protect(s)
    return any(m in p for m in MANDARIN_MARKERS) and not any(c in p for c in CANTONESE_MARKERS)


def fix_drift(s: str) -> str:
    p, keep = _protect(s)
    for a, b in DRIFT_MAP:
        p = p.replace(a, b)
    return _restore(p, keep)


@lru_cache(maxsize=4)
def _cc(variant: str):
    try:
        import opencc
        return opencc.OpenCC(variant)
    except Exception:  # the package is pinned in pyproject; never fail the reply over it
        return None


def _s2t():
    return _cc("s2t")


# HK colloquial characters that OpenCC would "convert" although they are correct Traditional Cantonese here
# (eval defect d: 晒吓搵郁 → 曬嚇揾鬱). They are shielded before the conversion and restored after it.
KEEP_CHARS = "晒吓搵郁了臺表斗咁噉系吃"


@lru_cache(maxsize=4096)
def _is_simplified_char(c: str) -> bool:
    conv = _s2t()
    return bool(conv) and c not in KEEP_CHARS and conv.convert(c) != c


def to_traditional(text: str, variant: str = "s2t") -> str:
    """Simplified → Traditional (OpenCC `variant`, phrase-aware), only when the text has a Simplified-only
    character; Traditional text is left exactly as written (no HK-variant rewriting such as 溫 → 温).
    Replies use `s2t`; live STT transcripts use `s2hk` (`ahbo.speech.transcript`). KEEP_CHARS are shielded in both."""
    conv = _cc(variant)
    if not conv or not text or not any(_is_simplified_char(c) for c in text if "\u4e00" <= c <= "\u9fff"):
        return text
    shielded = "".join(chr(0xF000 + KEEP_CHARS.index(c)) if c in KEEP_CHARS else c for c in text)
    out = conv.convert(shielded)
    return "".join(KEEP_CHARS[ord(c) - 0xF000] if 0xF000 <= ord(c) < 0xF000 + len(KEEP_CHARS) else c for c in out)


def _digit_string(s: str) -> str:
    return "".join(DIGITS[int(c)] for c in s)


def numbers_to_words(text: str) -> str:
    """Everything the shared converter doesn't know, then `digits_to_words` (times and counts)."""
    t = text.translate(FULLWIDTH_DIGITS)
    t = re.sub(r"(?<!\d)(\d{4})\s*年", lambda m: _digit_string(m.group(1)) + "年", t)
    t = re.sub(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*[日號]",
               lambda m: f"{int_words(int(m.group(1)))}月{int_words(int(m.group(2)))}號", t)
    t = re.sub(r"月\s*(\d{1,2})\s*日", lambda m: f"月{int_words(int(m.group(1)))}號", t)
    t = re.sub(r"(?<!\d)(\d{1,2})\s*[號]", lambda m: int_words(int(m.group(1))) + "號", t)
    t = re.sub(r"(?<!\d)(\d{1,3})\s*%", lambda m: "百分之" + int_words(int(m.group(1))), t)
    t = re.sub(r"(?:HK)?\$\s*(\d{1,5})(?!\d)", lambda m: int_words(int(m.group(1)), counting=True) + "蚊", t)
    t = re.sub(r"(?<![\d:：])(\d{1,5})\.(\d+)(?!\d)",
               lambda m: int_words(int(m.group(1)), counting=True) + "點" + _digit_string(m.group(2)), t)
    for unit, word in UNITS:
        t = re.sub(r"(\d)\s*" + re.escape(unit) + r"(?![A-Za-z])", r"\1" + word, t, flags=re.I)
    t = re.sub(r"(?<!\d)\d{6,}(?!\d)", lambda m: _digit_string(m.group(0)), t)
    return digits_to_words(t)


def clean(text: str, flags: list[str]) -> str:
    """Steps 1–3: everything except drift and length (shared by the whole-text and the stream filter)."""
    t = SYS_PREFIX_RE.sub("", text or "")        # a model echoing an injection prefix must never be spoken
    t2 = MD_LINK_RE.sub(r"\1", t)
    t2 = URL_RE.sub("", t2)
    t2 = EMAIL_RE.sub("", t2)
    if t2 != MD_LINK_RE.sub(r"\1", t):
        flags.append("url")
    t3 = MD_HEAD_RE.sub("", t2)
    t3 = MD_LIST_RE.sub("", t3)
    t3 = MD_CHARS_RE.sub("", t3)
    if t3 != t2:
        flags.append("markdown")
    t4 = EMOJI_RE.sub("", t3)
    if t4 != t3:
        flags.append("emoji")
    t = t4
    tr = to_traditional(t)
    if tr != t:
        flags.append("simplified")
        t = tr
    if re.search(r"[0-9０-９]", t):
        flags.append("digits_spoken")
        t = numbers_to_words(t)
    t = t.translate(ASCII_PUNCT)
    t = re.sub(r"\.{2,}|…+", "…", t)
    if LATIN_WORD_RE.search(t):
        flags.append("english")
        t = LATIN_WORD_RE.sub("", t)
    t = re.sub(r"\s+", "", t)
    t = re.sub(r"[，、]{2,}", "，", t)
    t = re.sub(r"^[，、。]+", "", t)
    return t


def _drift(s: str, flags: list[str], fix: bool) -> str:
    if is_mandarin_drift(s):
        flags.append("mandarin_drift")
        if fix:
            s = fix_drift(s)
    return s


def spoken_filter(text: str, *, max_sentences: int = 2, fix_drift: bool = True) -> FilterResult:
    flags: list[str] = []
    t = clean(text, flags)
    kept, long_count = [], 0
    for s in sentences(t):
        s = _drift(s, flags, fix_drift)
        is_tag = len(s) <= SHORT_TAG_CHARS          # 「有呀！」「想唔想去？」「到時我會提你㗎。」 (D-10)
        if not is_tag:
            if long_count >= max_sentences:
                flags.append("too_long")
                break
            long_count += 1
        kept.append(s)
    return FilterResult("".join(kept).strip(), list(dict.fromkeys(flags)))


class StreamFilter:
    """Incremental spoken filter for a streamed reply (LiveKit llm_node). Feed text with `push()`; it returns the
    filtered complete sentences ready for TTS. `stopped` turns True when the length guard wants the stream cut
    (the caller interrupts generation, §5.6 step 4). `flush()` returns what is left at the end."""

    def __init__(self, max_sentences: int = 2, fix: bool = True):
        self.max_sentences, self.fix = max_sentences, fix
        self.buf = ""
        self.long_count = 0
        self.flags: list[str] = []
        self.stopped = False
        self.spoken = ""

    def _emit(self, raw: str) -> str:
        if self.stopped:
            return ""
        s = _drift(clean(raw, self.flags), self.flags, self.fix)
        if not s:
            return ""
        if len(s) > SHORT_TAG_CHARS:
            if self.long_count >= self.max_sentences:
                self.flags.append("too_long")
                self.stopped = True
                return ""
            self.long_count += 1
        self.spoken += s
        return s

    def push(self, text: str) -> list[str]:
        self.buf += text
        out = []
        while True:
            # a sentence ends at 。！？ (ASCII ?! too); never split inside a URL or a number like 2.5
            m = re.search(r"[。！？?!]", self.buf)
            if not m:
                break
            raw, self.buf = self.buf[: m.end()], self.buf[m.end():]
            s = self._emit(raw)
            if s:
                out.append(s)
            if self.stopped:
                break
        return out

    def flush(self) -> str:
        raw, self.buf = self.buf, ""
        return self._emit(raw) if raw.strip() else ""

    @property
    def quality(self) -> list[str]:
        return [f for f in dict.fromkeys(self.flags) if f in QUALITY_EVENTS]
