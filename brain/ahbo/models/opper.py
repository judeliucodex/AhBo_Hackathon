"""Opper route (§20.3, §24.16): one OpenAI-compatible endpoint for every model (DeepSeek Flash, Claude Sonnet/Haiku).
`POST https://api.opper.ai/v3/compat/chat/completions`, `Authorization: Bearer <OPPER_API_KEY>` (a project key;
management keys `op-mak-…` are rejected). Native Anthropic fields (adaptive thinking, server web search,
output_config) do not exist on this route, so callers send plain chat-completions requests."""
from __future__ import annotations

from typing import Any

OPPER_URL = "https://api.opper.ai/v3/compat"


def async_client(settings: Any, *, timeout: float = 30.0) -> Any:
    from openai import AsyncOpenAI
    return AsyncOpenAI(api_key=settings.opper_api_key, base_url=OPPER_URL, max_retries=0, timeout=timeout)


def to_openai_tools(anthropic_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """{name, description, input_schema} → OpenAI function tools."""
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                              "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}}
            for t in anthropic_tools]
