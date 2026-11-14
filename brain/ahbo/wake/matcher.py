"""The Cantonese sound matcher for 「喂阿寶」 (PRD §20.4.3). Stage 3 (server) twin of `JyutpingMatcher.swift`.

Both implementations follow the same rules and are tested against the same fixture
(`brain/config/wake_cases.yaml`) and the same generated table (`ios/AhBo/Resources/wake_homophones.json`).

Rules (config: `brain/config/wake_match.yaml`):
  * Separators (whitespace, punctuation, symbols, control/format chars, i.e. Unicode general categories
    Z*, P*, S*, Cc, Cf) are skipped; the remaining characters are the syllables (1 Han char = 1 syllable).
  * Only the START of the utterance counts: syllable 0 must be 喂-class (`wai`) when `require_wai`.
  * After it: `aa bou` (aa/a, bou/bo via the table) at syllable 1 → wake phrase = syllables 0..2;
    or, with `allow_missing_aa`, `bou` at syllable 1 (「喂寶」) → syllables 0..1;
    or `aa bou` "within 2 syllables of the start": one filler syllable allowed between 喂 and 阿寶
    (「喂喂阿寶」, 「喂呀阿寶」) — the full `aa bou` is required on that path.
  * `plain_abou_wakes` (off by default): `aa bou` at syllable 0 (「阿寶」) also wakes.
  * Exact strings 「喂阿寶」 and 「喂，阿寶」 at the start always match (safety net if the table lacked a char).
  * **Reduplication guard:** if the syllable right after the matched `bou` is itself `bou`-class, it is NOT
    the wake word. 寶寶 (bou bou) is the word "baby" (「喂寶寶」 = "hey baby"), not 喂+阿寶. 「喂寶」 is accepted
    only through the missing-aa rule, and that rule is exactly the one 寶寶 would otherwise ride on, so the
    guard is what separates the two. The cost: 「喂阿寶寶…」 also stays asleep — acceptable (she says
    「喂阿寶」, and touch is always an equal path).
  * Remainder = the transcript after the last matched character, leading separators stripped
    (「喂阿寶，今日有咩做呀？」 → 「今日有咩做呀？」). Flash sees only the remainder (§20.4.3).
"""
from __future__ import annotations

import json
import logging
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

EXACT_STRINGS = ("喂阿寶", "喂，阿寶")
MAX_SYLLABLES = 6
DEFAULTS: dict[str, Any] = {"require_wai": True, "allow_missing_aa": True, "plain_abou_wakes": False,
                            "exact_only": False}


def is_separator(ch: str) -> bool:
    cat = unicodedata.category(ch)
    return cat[0] in ("Z", "P", "S") or cat in ("Cc", "Cf")


def strip_leading_separators(text: str) -> str:
    i = 0
    while i < len(text) and is_separator(text[i]):
        i += 1
    return text[i:]


@dataclass(frozen=True)
class WakeMatch:
    ok: bool
    remainder: str = ""
    rule: str = ""          # exact | full | missing_aa | filler | plain | ""
    matched: str = ""       # the wake syllables as written in the transcript (separators removed)


def load_homophones(path: Path | None = None) -> dict[str, frozenset[str]]:
    from .table import DEFAULT_OUT

    data = json.loads(Path(path or DEFAULT_OUT).read_text(encoding="utf-8"))
    return {k: frozenset(data[k]) for k in ("wai", "aa", "bou")}


class WakeMatcher:
    def __init__(self, homophones: dict[str, frozenset[str]] | None = None,
                 config: dict[str, Any] | None = None) -> None:
        self.h = homophones if homophones is not None else load_homophones()
        cfg = {**DEFAULTS, **(config or {})}
        self.require_wai = bool(cfg["require_wai"])
        self.allow_missing_aa = bool(cfg["allow_missing_aa"])
        self.plain_abou = bool(cfg["plain_abou_wakes"]) or not self.require_wai
        self.exact_only = bool(cfg.get("exact_only", False))

    def _is(self, cls: str, syl: list[tuple[str, int]], i: int) -> bool:
        return i < len(syl) and syl[i][0] in self.h[cls]

    def match(self, transcript: str | None) -> WakeMatch:
        text = transcript or ""
        # (char, index in text) for the first MAX_SYLLABLES non-separator characters
        syl: list[tuple[str, int]] = []
        for idx, ch in enumerate(text):
            if is_separator(ch):
                continue
            syl.append((ch, idx))
            if len(syl) >= MAX_SYLLABLES:
                break
        if not syl:
            return WakeMatch(False)

        candidates: list[tuple[str, int]] = []   # (rule, number of syllables in the wake phrase)
        if "".join(c for c, _ in syl).startswith("喂阿寶"):     # separators already skipped in syl
            candidates.append(("exact", 3))
        if not self.exact_only and self._is("wai", syl, 0):
            if self._is("aa", syl, 1) and self._is("bou", syl, 2):
                candidates.append(("full", 3))
            if self.allow_missing_aa and self._is("bou", syl, 1):
                candidates.append(("missing_aa", 2))
            if self._is("aa", syl, 2) and self._is("bou", syl, 3):
                candidates.append(("filler", 4))
        if self.plain_abou and self._is("aa", syl, 0) and self._is("bou", syl, 1):
            candidates.append(("plain", 2))

        for rule, n in candidates:
            if self._is("bou", syl, n):            # reduplication guard: 寶寶 = "baby"
                continue
            end = syl[n - 1][1] + 1
            return WakeMatch(True, strip_leading_separators(text[end:]), rule, "".join(c for c, _ in syl[:n]))
        return WakeMatch(False)


@lru_cache(maxsize=1)
def _default_matcher() -> WakeMatcher:
    from ahbo.core.config import load_config

    try:
        cfg = load_config().wake_match
    except Exception:   # config unreadable: the documented defaults still hold
        log.warning("wake_match.yaml unreadable; using defaults", exc_info=True)
        cfg = {}
    return WakeMatcher(config=cfg)


def stage3_check(transcript: str | None) -> tuple[bool, str]:
    """Stage 3 (§20.4.1): does the session's first zh-HK transcript begin with the wake phrase?

    Returns `(ok, remainder)`; the remainder (wake syllables stripped) is what Flash should see. The LiveKit
    worker closes the session silently and logs `wake.rejected_s3` when `ok` is False (or no transcript
    arrives within `stage3_timeout_s`). The transcript itself is never logged here (ambient-speech privacy).
    """
    m = _default_matcher().match(transcript)
    return m.ok, m.remainder
