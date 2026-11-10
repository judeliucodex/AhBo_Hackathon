"""LLM stand-ins for red-team and degraded cases. They implement the FlashLLM protocol (CONTRACTS §12), so their
tool calls go through the real tool layer and the real Kernel — exactly where a misbehaving model would land."""
from __future__ import annotations

from typing import Any

from ahbo.core.adapters import LLMResponse, ToolCall
from ahbo.core.ids import new_id


class ScriptedFlash:
    """Returns the scripted responses in order, one per `chat` call; then an empty reply (the turn ends).

    Script item: `{"text": "…", "tool_calls": [{"name": "…", "args": {…}}]}`.
    """
    model = "scripted-flash"

    def __init__(self, script: list[dict[str, Any]]):
        self.script = list(script)
        self.calls = 0

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, *,
                   purpose: str = "voice_turn", thinking: str | None = None,
                   correlation_id: str | None = None) -> LLMResponse:
        if purpose != "voice_turn":
            return LLMResponse(text="{}", model=self.model)
        self.calls += 1
        if not self.script:
            return LLMResponse(text="", model=self.model)
        item = self.script.pop(0)
        calls = [ToolCall(id=new_id("call"), name=c["name"], args=dict(c.get("args") or {}))
                 for c in item.get("tool_calls") or []]
        return LLMResponse(text=item.get("text", ""), tool_calls=calls, model=self.model)


class RaisingFlash:
    """Every model call fails (provider down, §10.4 / INV-3 drills)."""
    model = "raising-flash"

    async def chat(self, *_a: Any, **_k: Any) -> LLMResponse:
        raise RuntimeError("flash provider down (eval drill)")
