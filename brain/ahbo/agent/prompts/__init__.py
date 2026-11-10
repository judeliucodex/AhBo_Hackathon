"""Prompts (§5.4 persona verbatim, extra safety rules, §24.10 deep prompt) and the injection line format (§5.3).

The prompt texts live next to this file as Markdown so they can be tuned with the evals without touching code.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).parent
INJECTION_TYPES = ("提醒", "問候", "背景結果", "錄音", "行動結果", "家人")
SYSTEM_PREFIX = "[阿寶系統｜"


@lru_cache(maxsize=None)
def load(name: str) -> str:
    return (HERE / f"{name}.md").read_text(encoding="utf-8").strip()


def persona() -> str:
    """§5.4 verbatim."""
    return load("persona")


def rules() -> str:
    return load("rules")


def deep_prompt() -> str:
    """§24.10 deep agent prompt (DEEP_PROMPT)."""
    return load("deep")


def system_line(kind: str, content: str, instruction: str = "") -> str:
    """`[阿寶系統｜<TYPE>｜唔係王婆婆講嘅] <content>。<what to do>。` (§5.3). Unknown types fall back to 提醒."""
    kind = kind if kind in INJECTION_TYPES else "提醒"
    content = (content or "").strip()
    if content and content[-1] not in "。！？":
        content += "。"
    instruction = (instruction or "").strip()
    if instruction and instruction[-1] not in "。！？":
        instruction += "。"
    return f"[阿寶系統｜{kind}｜唔係王婆婆講嘅] {content}{instruction}"


def is_system_line(text: str) -> bool:
    return (text or "").startswith(SYSTEM_PREFIX)
