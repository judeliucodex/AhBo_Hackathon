"""`ahbo gen-wake-table` (PRD §25.6): build the wake-word homophone table from PyCantonese.

One generated file, `ios/AhBo/Resources/wake_homophones.json`, is read by BOTH matchers (the Swift
`JyutpingMatcher` bundles it; the Python Stage-3 matcher reads it from the repo). No hand copies.

Readings per character (PyCantonese 5.0.0, verified in the `ahbo-api` image):
  1. `pycantonese.characters_to_jyutping([ch])` — a one-element *list* so no word segmentation runs; returns
     `[(ch, "bou2")]` or `[(ch, None)]`. This is PyCantonese's single preferred reading (rime-cantonese
     overrides HKCanCor), e.g. 阿 → `o1`.
  2. plus every reading the HKCanCor corpus (`pycantonese.hkcancor()`) gives the character in at least
     `MIN_SHARE` of its aligned occurrences (and `MIN_COUNT` times). This restores common colloquial
     readings that (1) hides, e.g. 阿 → `aa3` (330 of 330 HKCanCor tokens), without the noise of rare
     word-level readings (e.g. rime's 婆 → `bo` inside one compound, which would make 喂阿婆 wake).
A character goes into a class when the toneless syllable of ANY kept reading is in that class:
  wai: {wai} · aa: {aa, a} · bou: {bou, bo}.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from pathlib import Path

from ahbo.core.settings import REPO_ROOT

log = logging.getLogger(__name__)

DEFAULT_OUT = REPO_ROOT / "ios" / "AhBo" / "Resources" / "wake_homophones.json"
CJK_RANGE = range(0x4E00, 0x9FFF + 1)          # CJK Unified Ideographs, U+4E00–U+9FFF
CLASSES: dict[str, frozenset[str]] = {
    "wai": frozenset({"wai"}),
    "aa": frozenset({"aa", "a"}),
    "bou": frozenset({"bou", "bo"}),
}
MIN_SHARE = 0.25
MIN_COUNT = 2
_TONE = re.compile(r"[1-6]$")


def toneless(syllable: str) -> str:
    return _TONE.sub("", syllable.strip())


def _corpus_readings() -> dict[str, set[str]]:
    """Toneless readings per character that HKCanCor uses often enough (MIN_SHARE, MIN_COUNT)."""
    import pycantonese

    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for token in pycantonese.hkcancor().tokens():
        if not token.word or not token.jyutping:
            continue
        try:
            syllables = pycantonese.parse_jyutping(token.jyutping)
        except ValueError:
            continue
        if len(syllables) != len(token.word):
            continue
        for ch, jp in zip(token.word, syllables):
            counts[ch][toneless(str(jp))] += 1
    out: dict[str, set[str]] = {}
    for ch, c in counts.items():
        total = sum(c.values())
        out[ch] = {s for s, n in c.items() if n >= MIN_COUNT and n / total >= MIN_SHARE}
    return out


def build_table() -> dict[str, list[str]]:
    import pycantonese

    corpus = _corpus_readings()
    classes: dict[str, set[str]] = {k: set() for k in CLASSES}
    for cp in CJK_RANGE:
        ch = chr(cp)
        readings: set[str] = set(corpus.get(ch, ()))
        for _, jp in pycantonese.characters_to_jyutping([ch]):
            if jp:
                readings.update(toneless(s) for s in jp.split())
        for name, bases in CLASSES.items():
            if readings & bases:
                classes[name].add(ch)
    return {name: sorted(chars) for name, chars in classes.items()}


def gen_table(out_paths: list[Path] | None = None) -> dict[str, list[str]]:
    """Generate the table and write it to each path (default: the one shared file). Returns the table."""
    table = build_table()
    text = json.dumps(table, ensure_ascii=False, indent=1) + "\n"
    for p in out_paths or [DEFAULT_OUT]:
        p = Path(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        log.info("wake table written: %s (%s)", p, {k: len(v) for k, v in table.items()})
    return table


def main(argv: list[str] | None = None) -> int:
    """`ahbo gen-wake-table [--out PATH ...]` (ops/cli.py delegates here)."""
    import argparse

    ap = argparse.ArgumentParser(prog="ahbo gen-wake-table", description=__doc__.splitlines()[0])
    ap.add_argument("--out", action="append", type=Path, help=f"output file (default: {DEFAULT_OUT})")
    args = ap.parse_args(argv or [])
    logging.basicConfig(level=logging.INFO)
    table = gen_table(args.out)
    log.info("classes: %s", {k: len(v) for k, v in table.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
