"""Wake matcher (§20.4.3, §25.6): fixture-driven, same cases as the Swift XCTest (JyutpingMatcherTests)."""
from __future__ import annotations

import json

import pytest
import yaml

from ahbo.core.settings import CONFIG_DIR
from ahbo.wake.matcher import WakeMatcher, load_homophones, stage3_check
from ahbo.wake.table import DEFAULT_OUT

CASES = yaml.safe_load((CONFIG_DIR / "wake_cases.yaml").read_text(encoding="utf-8"))
CFG = yaml.safe_load((CONFIG_DIR / "wake_match.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def matcher() -> WakeMatcher:
    return WakeMatcher(load_homophones(), CFG)


@pytest.mark.parametrize("text", CASES["accept"])
def test_accept(matcher: WakeMatcher, text: str) -> None:
    assert matcher.match(text).ok, text


@pytest.mark.parametrize("text", CASES["reject"])
def test_reject(matcher: WakeMatcher, text: str) -> None:
    assert not matcher.match(text).ok, text


@pytest.mark.parametrize("text,remainder", [
    ("喂阿寶", ""), ("喂，阿寶", ""), ("喂 阿寶", ""), ("喂寶", ""),
    ("喂阿寶，今日有咩做呀？", "今日有咩做呀？"), ("威阿寶 我幾時食藥呀", "我幾時食藥呀"),
    ("  喂阿寶。", ""),
])
def test_remainder(matcher: WakeMatcher, text: str, remainder: str) -> None:
    m = matcher.match(text)
    assert m.ok and m.remainder == remainder


def test_rules(matcher: WakeMatcher) -> None:
    assert matcher.match("喂寶").rule == "missing_aa"
    assert matcher.match("喂喂阿寶").rule == "filler"
    assert matcher.match("喂亞寶").rule == "full"
    assert matcher.match("喂阿寶").rule == "exact"
    assert not matcher.match("喂阿寶寶").ok          # reduplication guard (寶寶 = baby)


def test_switches() -> None:
    h = load_homophones()
    assert not WakeMatcher(h, CFG).match("阿寶，你好").ok
    plain = WakeMatcher(h, {**CFG, "plain_abou_wakes": True})
    m = plain.match("阿寶，你好")
    assert m.ok and m.remainder == "你好"
    assert not WakeMatcher(h, {**CFG, "allow_missing_aa": False}).match("喂寶").ok


def test_stage3_check() -> None:
    assert stage3_check("喂阿寶，今日有咩做呀？") == (True, "今日有咩做呀？")
    assert stage3_check("今日天氣好好") == (False, "")
    assert stage3_check(None) == (False, "")


def test_table_shape_and_key_chars() -> None:
    data = json.loads(DEFAULT_OUT.read_text(encoding="utf-8"))
    assert sorted(data) == ["aa", "bou", "wai"]
    for k, chars in data.items():
        assert chars == sorted(chars) and all(len(c) == 1 and 0x4E00 <= ord(c) <= 0x9FFF for c in chars), k
    assert {"喂", "威", "餵"} <= set(data["wai"])
    assert {"阿", "亞"} <= set(data["aa"])
    assert {"寶", "保"} <= set(data["bou"])
    for c in "婆爸飽":
        assert all(c not in v for v in data.values()), c


def test_table_is_current(tmp_path) -> None:
    """The committed JSON must equal a fresh generation (no hand edits; regenerate with gen-wake-table)."""
    from ahbo.wake.table import gen_table

    out = tmp_path / "t.json"
    gen_table([out])
    assert out.read_text(encoding="utf-8") == DEFAULT_OUT.read_text(encoding="utf-8")


def test_cases_yaml_is_swift_parsable() -> None:
    """The Swift test reads wake_cases.yaml directly: each `key: [...]` line must be a JSON array."""
    lines = [ln for ln in (CONFIG_DIR / "wake_cases.yaml").read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    parsed = {ln.split(":", 1)[0].strip(): json.loads(ln.split(":", 1)[1]) for ln in lines}
    assert parsed == CASES


def test_match_yaml_is_swift_parsable() -> None:
    """The app reads wake_match.yaml defaults with a `key: scalar` line parser."""
    for ln in (CONFIG_DIR / "wake_match.yaml").read_text(encoding="utf-8").splitlines():
        if ln.strip() and not ln.lstrip().startswith("#"):
            k, v = (s.strip() for s in ln.split(":", 1))
            assert k in CFG and str(CFG[k]).lower() == v.lower(), ln
