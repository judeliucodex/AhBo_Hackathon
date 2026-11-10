"""Adapter interfaces (CONTRACTS §12). Each external dependency has a real and a fake implementation behind
one of these Protocols, chosen at start-up from AHBO_FAKE (§26.3). The Kernel cannot tell them apart.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    reasoning_content: str | None = None   # DeepSeek thinking mode: must be sent back with tools (§20.1)
    plan: list[str] | None = None          # fake-only: tools the turn will call, in order (steps shown up front)


@runtime_checkable
class FlashLLM(Protocol):
    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, *,
                   purpose: str = "voice_turn", thinking: str | None = None,
                   correlation_id: str | None = None) -> LLMResponse: ...


@runtime_checkable
class DeepRunner(Protocol):
    async def run(self, task_id: str, goal: str, constraints: str, context: dict[str, Any], *,
                  purpose: str = "deep_task") -> dict[str, Any]: ...


@runtime_checkable
class Vision(Protocol):
    async def describe(self, photo_path: Path, *, correlation_id: str | None = None) -> str | None: ...


@runtime_checkable
class FixedTTS(Protocol):
    def render(self, text: str, out_path: Path) -> tuple[float, list[list[float]]]: ...


@runtime_checkable
class FamilyChannel(Protocol):
    async def send(self, *, kind: str, contact_id: str, chat_id: str | None, text: str,
                   photo_path: Path | None = None, buttons: list[dict[str, str]] | None = None,
                   screenshots: list[Path] | None = None, correlation_id: str | None = None) -> dict[str, Any]: ...


@runtime_checkable
class PhoneChannel(Protocol):
    async def call(self, *, to_e164: str, contact_id: str, call_id: str, clip_url: str,
                   correlation_id: str | None = None) -> dict[str, Any]: ...


@runtime_checkable
class SessionLauncher(Protocol):
    async def open(self, session_id: str, reason: str, mode: str) -> dict[str, Any]: ...


@runtime_checkable
class BrowserRunner(Protocol):
    async def run(self, action: dict[str, Any]) -> dict[str, Any]: ...
