"""Deterministic matchers (§4.3.1, §4.3.4). No model is ever consulted (INV-2).

- `match_ok(text)` → "help" | "ok" | None. Help wins over OK in the same utterance; 「冇事」 inside 「有冇事」 is not
  OK. Phrases come from `config/ok_phrases.yaml`.
- `match_redflag(text)` → (level, category, phrase) | None, from `config/redflags.yaml`. `scam` maps to level
  `urgent` (category `scam`); the highest level found wins.

Normalisation: NFKC (full-width → ASCII), punctuation and whitespace removed, simplified → traditional (OpenCC
`s2t`), ASCII lower-cased. The help check also runs on the un-converted variants, so a conversion quirk can never hide
a help phrase.
"""
from __future__ import annotations

import unicodedata
from functools import lru_cache
from typing import Any

from opencc import OpenCC

# Question / negated forms that contain an OK phrase but are not an OK (ok_phrases.yaml `rule`).
# Simplified/formal STT spellings are covered because the text is converted (s2t) first: 有没有事 → 有沒有事.
NOT_OK_CONTEXTS = ("有冇事", "有無事", "有沒有事", "有沒事", "有冇問題", "有無問題", "有沒有問題", "唔係冇事", "唔係無事",
                   "唔係沒事", "不是沒事", "不是沒有事", "點會冇事", "點會沒事")
LEVELS = ("info", "urgent", "emergency")


@lru_cache(maxsize=1)
def _cc() -> OpenCC:
    return OpenCC("s2t")


def _strip(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum()).lower()


def _variants(text: str) -> list[str]:
    raw = _strip(text or "")
    nf = _strip(unicodedata.normalize("NFKC", text or ""))
    return list(dict.fromkeys([raw, nf, _strip(_cc().convert(nf))]))


def normalise(text: str) -> str:
    """The canonical form the matchers compare (NFKC, no punctuation/space, traditional, lower-case)."""
    return _variants(text)[-1]


@lru_cache(maxsize=64)
def _phrases(items: tuple[str, ...]) -> tuple[str, ...]:
    out: list[str] = []
    for p in items:
        for v in _variants(str(p)):
            if v and v not in out:
                out.append(v)
    return tuple(out)


def _cfg_list(cfg: Any, key: str) -> tuple[str, ...]:
    if cfg is None:
        from ahbo.core.config import load_config
        cfg = load_config()
    src = cfg.ok_phrases if key in ("ok", "help") else cfg.redflags
    return tuple(str(x) for x in (src.get(key) or []))


def is_help(text: str, cfg: Any = None) -> bool:
    helps = _phrases(_cfg_list(cfg, "help"))
    return any(h in v for v in _variants(text) for h in helps)


def is_ok(text: str, cfg: Any = None) -> bool:
    """OK phrase present (ignores help; use match_ok for the decision)."""
    t = normalise(text)
    for ctx in NOT_OK_CONTEXTS:
        t = t.replace(ctx, "|")
    return any(p in t for p in _phrases(_cfg_list(cfg, "ok")))


def match_ok(text: str, cfg: Any = None) -> str | None:
    if not text or not text.strip():
        return None
    if is_help(text, cfg):
        return "help"
    if is_ok(text, cfg):
        return "ok"
    return None


def match_redflag(text: str, cfg: Any = None) -> tuple[str, str, str] | None:
    """Highest red flag in `text`: ("emergency"|"urgent", category, phrase)."""
    if not text:
        return None
    vs = _variants(text)
    best: tuple[str, str, str] | None = None
    for key, level in (("emergency", "emergency"), ("urgent", "urgent"), ("scam", "urgent")):
        for p in _phrases(_cfg_list(cfg, key)):
            if any(p in v for v in vs):
                cand = (level, "scam" if key == "scam" else "health", p)
                if best is None or LEVELS.index(cand[0]) > LEVELS.index(best[0]):
                    best = cand
                break
    return best


def raise_level(*levels: str | None) -> str:
    """The highest of the given levels (never lower)."""
    known = [lv for lv in levels if lv in LEVELS]
    return max(known, key=LEVELS.index) if known else "info"
