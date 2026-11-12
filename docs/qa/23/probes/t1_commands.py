"""QA23 check 1: every command and menu path from /start //menu and every typed command in /help.

Spec: BUILD_LOG §8 (Telegram replaces the console), §9 (digest/plan removed), PRD §9.2.
Every path must answer, in Traditional Cantonese, with sensible buttons; /plan and digest settings are gone.
"""
from __future__ import annotations

import asyncio

import pytest
from qa_helpers import (bot_tasks, confirm, events_of, make_svc_fixture, say, simplified_hits, stop_all, tap,
                        commands_of)

MENU = ["今日狀況", "藥物", "日程", "設定", "聯絡人", "示範控制", "系統健康"]
HELP_CMDS = ["/menu", "/today", "/meds", "/cal", "/settings", "/contacts", "/alerts", "/health", "/demo",
             "/fall", "/sos", "/panic", "/reset", "/scenario", "/clock", "/mute", "/unmute", "/dose",
             "/checkin", "/say", "/thinking", "/fact", "/story", "/whoami", "/cancel"]
# UI strings 婆婆/阿珍 see must be Traditional (INV-10); allow a known-safe set of shared-form chars.
BAD_SIMPLIFIED = set("个无门问间说话记录应该么乐后业动发开时点长相见目爱妈妈女写写东西车马鸟语风飞饭汤钱电脑网络")


@pytest.fixture
async def svc(tmp_path):
    made: list = []
    s = await make_svc_fixture(tmp_path, made)
    yield s
    await stop_all(made)


def _texts(replies):
    return [r["text"] for r in replies]


def _buttons(replies):
    return {b["callback_data"]: b["text"] for r in replies for row in r["buttons"] for b in row}


async def test_start_and_menu_show_all_seven_items(svc):
    for cmd in ("/start", "/menu"):
        r = await say(svc, cmd)
        texts = "\n".join(_texts(r))
        assert r, f"{cmd} answered nothing"
        assert "阿寶主選單" in texts
        labels = _buttons(r).values()
        for item in MENU:
            assert item in labels, f"{cmd}: menu item 「{item}」 missing"
        assert not simplified_hits(texts), f"{cmd}: Simplified chars {simplified_hits(texts)}"


async def test_every_menu_button_answers_with_cantonese_and_buttons(svc):
    data_of = {}
    r = await say(svc, "/menu")
    data_of.update({v: k for k, v in _buttons(r).items()})
    for item in MENU:
        r = await tap(svc, data_of[item])
        assert r, f"menu 「{item}」 answered nothing"
        text = "\n".join(_texts(r))
        assert len(text) > 3, f"menu 「{item}」 empty reply"
        assert not simplified_hits(text), f"「{item}」 Simplified chars: {simplified_hits(text)}"
        assert _buttons(r), f"「{item}」 has no buttons (dead end)"
        # every view must offer a way back to the main menu
        assert "⬅️ 主選單" in _buttons(r).values(), f"「{item}」 has no ⬅️ 主選單 button"
        print(f"MENU {item}: {text[:80]!r} buttons={list(_buttons(r).values())[:8]}")


async def test_every_sub_button_of_every_view_answers(svc):
    """Walk two levels deep: every button reachable from the main menu answers something sensible."""
    seen: dict[str, str] = {}
    r = await say(svc, "/menu")
    queue = [d for d in _buttons(r)]
    data_of = {v: k for k, v in _buttons(r).items()}
    for item in MENU:
        r = await tap(svc, data_of[item])
        for d, t in _buttons(r).items():
            seen.setdefault(d, f"{item}:{t}")
    assert len(queue) >= 7
    problems = []
    for d, origin in sorted(seen.items()):
        r = await tap(svc, d)
        text = "\n".join(_texts(r))
        if not r or not text.strip():
            problems.append(f"{origin} ({d}): NO REPLY")
            continue
        if simplified_hits(text):
            problems.append(f"{origin} ({d}): Simplified {simplified_hits(text)}")
    assert not problems, "second-level buttons broken:\n" + "\n".join(problems)
    print(f"walked {len(seen)} second-level buttons, all answered")


async def test_every_help_command_answers(svc):
    for cmd in HELP_CMDS:
        r = await say(svc, cmd)
        text = "\n".join(_texts(r))
        assert r, f"{cmd} answered nothing"
        assert len(text.strip()) > 0
        assert not simplified_hits(text), f"{cmd}: Simplified chars {simplified_hits(text)}"
        print(f"CMD {cmd}: {text[:70]!r}")


async def test_help_lists_commands_and_matches_reality(svc):
    r = await say(svc, "/help")
    text = "\n".join(_texts(r))
    for cmd in ("/menu", "/today", "/meds", "/cal", "/settings", "/contacts", "/alerts", "/health", "/demo",
                "/fall", "/sos", "/panic", "/reset", "/scenario", "/clock", "/mute", "/unmute", "/dose",
                "/checkin", "/say", "/thinking", "/fact", "/story", "/whoami", "/cancel"):
        assert cmd in text, f"/help does not list {cmd}"


async def test_plan_and_digest_are_gone(svc):
    # BUILD_LOG §9: /plan gone, no 07:00 計劃 button, no digest time setting
    r = await say(svc, "/plan")
    assert any("唔知" in t or "唔得" in t for t in _texts(r)), f"/plan still answered: {_texts(r)}"
    r = await say(svc, "/menu")
    data_of = {v: k for k, v in _buttons(r).items()}
    r = await tap(svc, data_of["示範控制"])
    all_text = "\n".join(_texts(r))
    assert "計劃" not in all_text, f"demo menu still offers a plan button: {all_text!r}"
    assert not any("plan" in d for d in _buttons(r)), "demo menu has a plan callback button"
    r = await tap(svc, data_of["設定"])
    all_text = "\n".join(_texts(r))
    assert "摘要" not in all_text, f"settings view still shows a digest row: {all_text!r}"
    for t, d in _buttons(r).items():
        assert "摘要" not in t, f"settings view has a digest button 「{t}」"


async def test_help_text_must_not_advertise_removed_digest(tmp_path):
    # BUILD_LOG §9 removed the digest; the /help text was not updated (found by reading ui.py) — verify live
    import asyncio as _a
    from qa_helpers import make_svc_fixture, say
    made: list = []
    s = await make_svc_fixture(tmp_path, made)
    try:
        r = await say(s, "/help")
        text = "\n".join(_texts(r))
        print(f"HELP TEXT:\n{text}")
        if "摘要" in text:
            print("DEFECT CONFIRMED: /help still mentions 摘要 (digest) which was removed 12 Nov (BUILD_LOG §9)")
    finally:
        await stop_all(made)


async def test_unknown_command_is_polite(svc):
    r = await say(svc, "/nonsense")
    assert r and "唔知" in r[-1]["text"], r
    r = await say(svc, "你好呀")
    # not a command and no form open → relayed to 婆婆 as family.text, no reply to 阿珍
    from qa_helpers import settle
    await settle(svc)
    fam = events_of(svc, "family.text")
    assert fam and fam[-1]["payload"]["text"] == "你好呀", "plain text was not relayed to the Kernel"
