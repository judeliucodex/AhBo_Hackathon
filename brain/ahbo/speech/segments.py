"""Caption segments for fixed clips (§10.3, §23.5).

A segment is `[firstChar, endChar, startSec, endSec]`: while the audio clock is between startSec and endSec the
face reveals characters firstChar..endChar (endChar exclusive), counted in **UTF-16 code units** (the face and the
Swift app count that way). Two sources:

- `estimate_segments(text)`: fake speech. 0.19 s per character (punctuation included, so a comma or full stop
  becomes a pause of the same length); one segment per Han character or per run of Latin letters/digits.
- `boundaries_to_segments(...)`: real Azure word-boundary events.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

SEC_PER_CHAR = 0.19
TICKS_PER_SEC = 10_000_000   # Azure audio_offset / duration ticks are 100 ns

# Opening brackets/quotes are revealed together with the character after them.
OPENERS = set("「『（(〈《【“‘[")


def u16len(s: str) -> int:
    """Length of `s` in UTF-16 code units (non-BMP characters count 2)."""
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in s)


def u16_offsets(text: str) -> list[int]:
    """UTF-16 offset of each code point of `text`, plus the total length at the end."""
    out, n = [], 0
    for ch in text:
        out.append(n)
        n += 2 if ord(ch) > 0xFFFF else 1
    out.append(n)
    return out


def is_punct(ch: str) -> bool:
    return unicodedata.category(ch)[0] in ("P", "Z", "S") or ch.isspace()


def _is_word_char(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def _r(x: float) -> float:
    return round(x, 3)


def estimate_segments(text: str, sec_per_char: float = SEC_PER_CHAR) -> tuple[float, list[list[float]]]:
    """Estimated (duration, segments) for `text` with no real audio timing.

    duration = sec_per_char × characters. Each spoken unit (a Han character, or a run of ASCII letters/digits)
    gets one segment of sec_per_char per character; punctuation advances the clock (a pause) and is revealed
    with the unit before it, opening quotes with the unit after it.
    """
    offs = u16_offsets(text)
    chars = list(text)
    segs: list[list[float]] = []
    t = 0.0
    i = 0
    pending_open: int | None = None      # UTF-16 start of opening punctuation waiting for its unit
    while i < len(chars):
        ch = chars[i]
        if is_punct(ch):
            if ch in OPENERS and pending_open is None:
                pending_open = offs[i]
            elif segs and pending_open is None:
                segs[-1][1] = offs[i + 1]          # reveal the punctuation with the previous unit
            t += sec_per_char                      # the pause
            i += 1
            continue
        j = i + 1
        if _is_word_char(ch):
            while j < len(chars) and _is_word_char(chars[j]):
                j += 1
        start_char = pending_open if pending_open is not None else offs[i]
        pending_open = None
        dur = sec_per_char * (j - i)
        segs.append([start_char, offs[j], _r(t), _r(t + dur)])
        t += dur
        i = j
    if pending_open is not None and segs:
        segs[-1][1] = offs[-1]
    duration = _r(sec_per_char * len(chars))
    return duration, [[int(a), int(b), s, e] for a, b, s, e in segs]


@dataclass(frozen=True)
class Boundary:
    """One Azure word-boundary event, unit-normalised to what the SDK reports (VERIFY results in the report):
    audio_offset and duration in 100-ns ticks; text_offset and word_length in characters; `text` the word."""
    audio_offset: int
    duration: int
    text_offset: int
    word_length: int
    text: str
    kind: str = "word"     # word | punctuation | sentence


def boundaries_to_segments(boundaries: list[Boundary], text: str, total_duration: float,
                           ssml_text_start: int | None = None) -> list[list[float]]:
    """Turn Azure word boundaries into face segments over `text` (the plain line, not the SSML).

    Offsets are located by searching each boundary's `text` in the plain line from a running cursor, so the
    result is right whether the SDK reports offsets into the SSML or the plain text and whatever escaping the
    SSML needed; `text_offset - ssml_text_start` is used only as a hint when the word occurs more than once.
    Punctuation boundaries extend the previous segment instead of making their own. Segments never overlap
    and never run past `total_duration`.
    """
    offs = u16_offsets(text)
    segs: list[list[float]] = []
    cursor = 0                      # code-point index into text
    evs = sorted((b for b in boundaries if b.kind != "sentence"), key=lambda b: b.audio_offset)
    for b in evs:
        word = b.text or ""
        if not word:
            continue
        hint = None
        if ssml_text_start is not None and b.text_offset >= 0:
            hint = b.text_offset - ssml_text_start
        pos = -1
        if hint is not None and 0 <= hint and text.startswith(word, hint) and hint >= cursor:
            pos = hint
        if pos < 0:
            pos = text.find(word, cursor)
        if pos < 0:
            continue                # a word that isn't in the line (e.g. SSML-only text): skip it
        end = pos + len(word)
        start_s = b.audio_offset / TICKS_PER_SEC
        end_s = (b.audio_offset + max(b.duration, 0)) / TICKS_PER_SEC
        punct_only = all(is_punct(c) for c in word)
        if b.kind == "punctuation" or punct_only:
            if segs:
                segs[-1][1] = max(segs[-1][1], offs[end])
            cursor = end
            continue
        if segs and start_s < segs[-1][3]:
            start_s = segs[-1][3]
        end_s = max(end_s, start_s + 0.01)
        first = offs[pos]
        # an opening quote just before this word is revealed with it
        if segs and pos > cursor:
            gap = text[cursor:pos]
            if gap and all(is_punct(c) for c in gap):
                if all(c in OPENERS or c.isspace() for c in gap):
                    first = offs[cursor]
                else:
                    segs[-1][1] = offs[pos]
        elif not segs and pos > 0 and all(is_punct(c) for c in text[:pos]):
            first = 0
        segs.append([first, offs[end], start_s, end_s])
        cursor = end
    if segs:
        # trailing punctuation shows with the last word
        if all(is_punct(c) for c in text[cursor:]):
            segs[-1][1] = offs[-1]
        if total_duration > 0:
            for s in segs:
                s[2] = min(s[2], total_duration)
                s[3] = min(s[3], total_duration)
    return [[int(a), int(b), _r(s), _r(e)] for a, b, s, e in segs]
