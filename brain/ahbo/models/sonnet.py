"""Claude Sonnet 5.5 call configuration (§20.2) with the official Anthropic SDK (anthropic 2.7.0, verified symbols:
`AsyncAnthropic().beta.messages.stream(...)` → `BetaAsyncMessageStreamManager`; `await stream.get_final_message()`;
params `thinking`, `output_config`, `betas`, `fallbacks`, `tool_choice`, `system`, `tools`).

Thinking knob (§20.2 table):
    off            → thinking={"type": "between_tools"}  (no other field), output_config.effort = "high"
    low … max      → thinking={"type": "adaptive", "display": <display>}, output_config.effort = <level>
`budget_tokens`, temperature/top_p/top_k and forced tool_choice are never sent (400 on Sonnet 5.5).
Precedence: explicit level > console knob (kv `knobs.sonnet_thinking`) > models.yaml per-purpose > sonnet.thinking.
"""
from __future__ import annotations

from typing import Any

from . import runtime

LEVELS = ("off", "low", "medium", "high", "xhigh", "max")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
TASK_BUDGET_BETA = "task-budgets-2026-03-13"


def norm_level(level: str | None) -> str:
    lv = (level or "medium").lower()
    return lv if lv in LEVELS else "medium"


def thinking_param(level: str | None, display: str | None = "omitted") -> dict[str, Any]:
    lv = norm_level(level)
    if lv == "off":
        return {"type": "between_tools"}
    return {"type": "adaptive", "display": display if display in ("summarized", "omitted") else "omitted"}


def effort_param(level: str | None) -> str:
    lv = norm_level(level)
    return "high" if lv == "off" else lv


def sonnet_cfg(config: Any, purpose: str = "deep_task", level: str | None = None) -> dict[str, Any]:
    cfg = runtime.model_cfg(config, "sonnet", purpose)
    knob = runtime.knobs().get("sonnet_thinking")
    cfg["thinking"] = norm_level(level or knob or cfg.get("thinking"))
    return cfg


def request_kwargs(cfg: dict[str, Any], *, system: str, tools: list[dict[str, Any]],
                   messages: list[dict[str, Any]], schema: dict[str, Any] | None) -> dict[str, Any]:
    """The §20.2 reference call shape. `schema` → output_config.format (json_schema); None = the submit_result
    fallback (the schema then lives in a strict custom tool)."""
    output_config: dict[str, Any] = {"effort": effort_param(cfg["thinking"])}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    betas: list[str] = []
    kw: dict[str, Any] = {
        "model": cfg.get("model", "claude-sonnet-5-5"),
        "max_tokens": int(cfg.get("max_tokens", 16000)),
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],   # static → cached
        "tools": tools,
        "tool_choice": {"type": "auto"},
        "thinking": thinking_param(cfg["thinking"], cfg.get("display")),
        "output_config": output_config,
        "messages": messages,
    }
    if cfg.get("use_task_budget") and int(cfg.get("task_budget_tokens") or 0) >= 20000:
        output_config["task_budget"] = {"type": "tokens", "total": int(cfg["task_budget_tokens"])}
        betas.append(TASK_BUDGET_BETA)
    if cfg.get("provider", "anthropic") == "anthropic" and cfg.get("server_fallback", True):
        kw["fallbacks"] = "default"           # refusal → server-side fallback; Claude API only (§20.2)
        betas.append(FALLBACK_BETA)
    if betas:
        kw["betas"] = betas
    return kw


def server_tools(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": int(cfg.get("web_search_max_uses", 5)),
         "user_location": {"type": "approximate", "city": "Hong Kong", "country": "HK",
                           "timezone": "Asia/Hong_Kong"}},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": int(cfg.get("web_fetch_max_uses", 5))},
    ]


def make_client(settings: Any) -> Any:
    if (runtime.model_cfg(None, "sonnet").get("provider") or "anthropic") != "anthropic":
        raise NotImplementedError("Sonnet over Opper is not wired (native §20.2 fields need the Anthropic API)")
    from anthropic import AsyncAnthropic
    return AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=1, timeout=60.0)
