"""Real Flash (§20.1, §24.4): DeepSeek V4.1 Flash through its OpenAI-compatible API with the OpenAI SDK (openai
3.48.0), streaming. Opper (`provider: opper`, §24.16) uses the same code with Opper's compat base URL and key.

- The thinking knob is ALWAYS sent (DeepSeek defaults to thinking on/high otherwise):
    off  → extra_body={"thinking": {"type": "disabled"}}
    low|high|max → extra_body={"thinking": {"type": "enabled"}}, reasoning_effort=<level>
  `thinking` is not an OpenAI SDK parameter, so it goes in `extra_body`; `reasoning_effort` is a native SDK
  parameter in openai 3.48.0 (Literal none|minimal|low|medium|high|xhigh|max).
  Precedence: the call's `thinking` argument > the console knob (kv `knobs.flash_thinking`, voice turns only) >
  `models.yaml` per-purpose > `models.yaml` flash.thinking.
- reasoning_content round-trip: assistant messages keep their `reasoning_content` (the runner stores it); it is
  sent back on every later request, as DeepSeek requires when tools + thinking are used (else HTTP 400).
- 5 s first-token timeout (models.yaml `first_token_timeout_s`) or any error → one retry on the fallback
  (`claude-haiku-5-5` via the Anthropic SDK, messages/tools converted).
- Costs and spans go through `ahbo.trace` when present (models.runtime.record).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from ahbo.core.adapters import LLMResponse, ToolCall
from ahbo.core.ids import new_id

from . import pricing, runtime

log = logging.getLogger("ahbo.models.flash")

DEEPSEEK_URL = "https://api.deepseek.com"
from .opper import OPPER_URL  # noqa: E402,F401  (re-exported; one source)
THINKING_LEVELS = ("off", "low", "high", "max")


def thinking_fields(level: str | None) -> dict[str, Any]:
    """§20.1 table → request kwargs (always includes extra_body.thinking)."""
    lv = (level or "off").lower()
    if lv not in THINKING_LEVELS:
        lv = {"minimal": "low", "medium": "high", "xhigh": "high", "ultra": "max"}.get(lv, "off")
    if lv == "off":
        return {"extra_body": {"thinking": {"type": "disabled"}}}
    return {"extra_body": {"thinking": {"type": "enabled"}}, "reasoning_effort": lv}


def resolve_thinking(cfg: dict[str, Any], purpose: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    knob = runtime.knobs().get("flash_thinking") if purpose == "voice_turn" else None
    return str(knob or cfg.get("thinking") or "off")


def clean_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI chat format; keeps `reasoning_content` on assistant turns (DeepSeek round-trip)."""
    out = []
    for m in messages:
        mm = {k: v for k, v in m.items() if k in ("role", "content", "tool_calls", "tool_call_id", "name",
                                                   "reasoning_content")}
        if mm.get("role") != "assistant":
            mm.pop("reasoning_content", None)
        out.append(mm)
    return out


# ---------------------------------------------------------------- OpenAI → Anthropic (fallback)
def to_anthropic(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
                 ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    system = "\n\n".join(str(m.get("content") or "") for m in messages if m["role"] == "system")
    out: list[dict[str, Any]] = []

    def push(role: str, block: dict[str, Any]) -> None:
        if out and out[-1]["role"] == role:
            out[-1]["content"].append(block)
        else:
            out.append({"role": role, "content": [block]})

    for m in messages:
        r = m["role"]
        if r == "system":
            continue
        if r == "user":
            push("user", {"type": "text", "text": str(m.get("content") or "")})
        elif r == "assistant":
            if m.get("content"):
                push("assistant", {"type": "text", "text": str(m["content"])})
            for tc in m.get("tool_calls") or []:
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except ValueError:
                    args = {}
                push("assistant", {"type": "tool_use", "id": tc["id"], "name": tc["function"]["name"], "input": args})
        elif r == "tool":
            push("user", {"type": "tool_result", "tool_use_id": m.get("tool_call_id", ""),
                          "content": str(m.get("content") or "")})
    atools = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
               "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}}}
              for t in tools or []]
    return system, out, atools


class RealFlash:
    def __init__(self, settings: Any, config: Any = None, *, client: Any = None, fallback_client: Any = None):
        self.settings, self.config = settings, config
        self._client, self._fallback = client, fallback_client
        cfg = runtime.model_cfg(config, "flash")
        self.model = cfg.get("model", "deepseek-flash")

    # ---- clients (lazy: importing this module never needs a key)
    def client(self, provider: str) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI
            if provider == "opper":
                self._client = AsyncOpenAI(api_key=self.settings.opper_api_key, base_url=OPPER_URL, max_retries=0,
                                           timeout=30.0)
            else:
                self._client = AsyncOpenAI(api_key=self.settings.deepseek_api_key,
                                           base_url=self.settings.deepseek_base_url or DEEPSEEK_URL, max_retries=0,
                                           timeout=30.0)
        return self._client

    def fallback_client(self) -> Any:
        if self._fallback is None:
            from anthropic import AsyncAnthropic
            self._fallback = AsyncAnthropic(api_key=self.settings.anthropic_api_key, max_retries=0, timeout=30.0)
        return self._fallback

    # ---- FlashLLM protocol
    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, *,
                   purpose: str = "voice_turn", thinking: str | None = None,
                   correlation_id: str | None = None) -> LLMResponse:
        cfg = runtime.model_cfg(self.config, "flash", purpose)
        level = resolve_thinking(cfg, purpose, thinking)
        timeout = float(cfg.get("first_token_timeout_s", 5))
        t0 = time.perf_counter()
        try:
            resp = await self._primary(messages, tools, cfg, level, timeout)
            self._record(cfg.get("provider", "deepseek"), resp, purpose, correlation_id, t0, level)
            return resp
        except Exception as e:
            log.warning("flash primary failed (%s); retrying once on the fallback", type(e).__name__)
            fb = cfg.get("fallback") or {}
            if not fb:
                raise
            resp = await self._fallback_call(messages, tools, cfg, fb)
            self._record(fb.get("provider", "anthropic"), resp, purpose, correlation_id, t0, level, fallback=True)
            return resp

    async def _primary(self, messages, tools, cfg, level, timeout) -> LLMResponse:
        provider = cfg.get("provider", "deepseek")
        kwargs: dict[str, Any] = {"model": cfg.get("model", "deepseek-flash"), "messages": clean_messages(messages),
                                  "max_tokens": int(cfg.get("max_tokens", 400)), "stream": True,
                                  "stream_options": {"include_usage": True}}
        if cfg.get("send_thinking", True):
            kwargs.update(thinking_fields(level))
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        client = self.client(provider)

        async def start():
            stream = await client.chat.completions.create(**kwargs)
            it = stream.__aiter__()
            first = await it.__anext__()
            return it, first

        it, first = await asyncio.wait_for(start(), timeout)     # first-token timeout (§24.4)
        text, reasoning = [], []
        calls: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] = {}

        def eat(chunk: Any) -> None:
            if getattr(chunk, "usage", None):
                u = chunk.usage
                usage.update({"input_tokens": u.prompt_tokens or 0, "output_tokens": u.completion_tokens or 0})
                det = getattr(u, "prompt_tokens_details", None)
                if det is not None and getattr(det, "cached_tokens", None):
                    usage["cache_read"] = det.cached_tokens
            for ch in chunk.choices or []:
                d = ch.delta
                if d is None:
                    continue
                if d.content:
                    text.append(d.content)
                rc = getattr(d, "reasoning_content", None) or (d.model_extra or {}).get("reasoning_content")
                if rc:
                    reasoning.append(rc)
                for tc in d.tool_calls or []:
                    slot = calls.setdefault(tc.index, {"id": None, "name": "", "args": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function is not None:
                        slot["name"] += tc.function.name or ""
                        slot["args"] += tc.function.arguments or ""

        eat(first)
        async for chunk in it:
            eat(chunk)
        tool_calls = []
        for _, c in sorted(calls.items()):
            try:
                args = json.loads(c["args"] or "{}")
            except ValueError:
                args = {"_raw": c["args"]}       # the tool layer answers bad_args
            tool_calls.append(ToolCall(id=c["id"] or new_id("call"), name=c["name"], args=args))
        return LLMResponse(text="".join(text), tool_calls=tool_calls, usage=usage, model=kwargs["model"],
                           reasoning_content="".join(reasoning) or None)

    async def _fallback_call(self, messages, tools, cfg, fb) -> LLMResponse:
        if fb.get("provider") == "opper":
            return await self._fallback_opper(messages, tools, cfg, fb)
        system, amsgs, atools = to_anthropic(messages, tools)
        kwargs: dict[str, Any] = {"model": fb.get("model", "claude-haiku-5-5"),
                                  "max_tokens": int(cfg.get("max_tokens", 400)), "messages": amsgs}
        if system:
            kwargs["system"] = system
        if atools:
            kwargs["tools"] = atools
            kwargs["tool_choice"] = {"type": "auto"}
        if fb.get("effort"):
            kwargs["output_config"] = {"effort": fb["effort"]}
        msg = await asyncio.wait_for(self.fallback_client().messages.create(**kwargs),
                                     float(cfg.get("fallback_timeout_s", 15)))
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        calls = [ToolCall(id=b.id, name=b.name, args=dict(b.input or {})) for b in msg.content
                 if getattr(b, "type", "") == "tool_use"]
        u = msg.usage
        usage = {"input_tokens": u.input_tokens or 0, "output_tokens": u.output_tokens or 0,
                 "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0}
        return LLMResponse(text=text, tool_calls=calls, usage=usage, model=kwargs["model"])

    async def _fallback_opper(self, messages, tools, cfg, fb) -> LLMResponse:
        """Haiku through Opper's OpenAI-compatible route (non-streaming; no DeepSeek thinking field)."""
        if self._fallback is None:
            from .opper import async_client
            self._fallback = async_client(self.settings)
        kwargs: dict[str, Any] = {"model": fb.get("model", "claude-haiku-5-5"), "messages": [
            {k: v for k, v in m.items() if k != "reasoning_content"} for m in clean_messages(messages)],
            "max_tokens": int(cfg.get("max_tokens", 400))}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        r = await asyncio.wait_for(self._fallback.chat.completions.create(**kwargs),
                                   float(cfg.get("fallback_timeout_s", 15)))
        msg = r.choices[0].message
        calls = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except ValueError:
                args = {"_raw": tc.function.arguments}
            calls.append(ToolCall(id=tc.id or new_id("call"), name=tc.function.name, args=args))
        u = r.usage
        usage = {"input_tokens": getattr(u, "prompt_tokens", 0) or 0, "output_tokens": getattr(u, "completion_tokens", 0) or 0}
        return LLMResponse(text=msg.content or "", tool_calls=calls, usage=usage, model=kwargs["model"])

    def _record(self, provider: str, resp: LLMResponse, purpose: str, corr: str | None, t0: float, level: str,
                fallback: bool = False) -> None:
        u = resp.usage or {}
        cost = pricing.usd(resp.model, input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0),
                           cache_read=u.get("cache_read", 0))
        runtime.record(provider, resp.model, purpose, u, cost, correlation_id=corr, span=f"llm.flash.{purpose}",
                       duration_ms=(time.perf_counter() - t0) * 1000,
                       attrs={"thinking": level, "fallback": fallback, "tool_calls": len(resp.tool_calls)})


def build_flash(settings: Any) -> RealFlash:
    """Used by `ahbo.api.services._flash` when models are not faked."""
    return RealFlash(settings)
