"""PII filter (§7.2, §11): never store HKID numbers, card/account numbers, phone numbers, passwords or e-mail
addresses. Deliberately over-rejects: a false positive only means a fact is not stored.

Spoken Chinese digits (「九一二三四五六七」) are mapped to ASCII digits before matching, so STT output that writes a
phone number in characters is caught too.
"""
from __future__ import annotations

import re
import unicodedata

REDACTED = "〔已隱藏〕"

_CN_DIGITS = str.maketrans("零〇一二三四五六七八九", "00123456789")
# HKID: one or two letters, six digits, check digit (0-9 or A) optionally in brackets: A123456(7), AB987654(3)
_HKID = re.compile(r"(?<![A-Za-z])[A-Za-z]{1,2}\s?\d{6}\s?[\(（]?\s?[0-9Aa]\s?[\)）]?(?!\d)")
# Any run of ≥ 8 digits, optionally separated by single spaces/dashes: phones (8), cards (13–19), accounts
_LONG_DIGITS = re.compile(r"(?<!\d)\+?\d(?:[ \-]?\d){7,}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PASSWORD = re.compile(r"密碼|密码|password|passcode|passwd|\bpin\b|登入碼|驗證碼|验证码|\botp\b|保安編碼|提款卡號|信用卡號|身份證號",
                       re.IGNORECASE)


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").translate(_CN_DIGITS)


def _char_norm(ch: str) -> str:
    n = unicodedata.normalize("NFKC", ch)
    return n if len(n) == 1 else ch


def find_pii(text: str | None) -> list[str]:
    """Kinds of PII found in `text` (empty list = clean)."""
    t = _norm(text or "")
    kinds = []
    if _HKID.search(t):
        kinds.append("hkid")
    if _LONG_DIGITS.search(t):
        kinds.append("number")       # phone, card or account number
    if _EMAIL.search(t):
        kinds.append("email")
    if _PASSWORD.search(t):
        kinds.append("password")
    return kinds


def has_pii(text: str | None) -> bool:
    return bool(find_pii(text))


def redact(text: str | None) -> str:
    """Replace PII spans with 〔已隱藏〕 (used on transcripts before they go to the extractor model, and on summaries).
    Matching runs on a per-character normalised copy (full-width → half-width, Chinese digits → digits) of the
    same length, so spans map back onto the original text, which is otherwise left exactly as it was."""
    if not text:
        return ""
    src = text
    t = "".join(_char_norm(ch) for ch in src).translate(_CN_DIGITS)
    spans: list[tuple[int, int]] = []
    for rx in (_HKID, _LONG_DIGITS, _EMAIL):
        spans += [m.span() for m in rx.finditer(t)]
    if _PASSWORD.search(t):
        # redact from the keyword to the end of its clause
        for m in _PASSWORD.finditer(t):
            end = len(t)
            for i in range(m.end(), len(t)):
                if t[i] in "。，,.!！?？\n；;":
                    end = i
                    break
            spans.append((m.start(), end))
    if not spans:
        return src
    spans.sort()
    out, pos = [], 0
    for s, e in spans:
        s = max(s, pos)
        if s >= e:
            continue
        out.append(src[pos:s])
        out.append(REDACTED)
        pos = e
    out.append(src[pos:])
    return "".join(out)
