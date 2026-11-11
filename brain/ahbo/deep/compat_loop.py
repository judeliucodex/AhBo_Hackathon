"""The deep agent over an OpenAI-compatible route (Opper, §20.3 / §24.16): Claude Sonnet 5.5 with our read-only
tools and the same JSON contract (§24.8.3), limits (15 steps, 90 s, $0.30) and validation as `deep/loop.py`.

Differences from the native Anthropic loop, because this route is plain chat completions:
- no server-side web_search / web_fetch: research uses the seeded activities, HKO weather, calendar and profile;
- no adaptive-thinking / output_config fields: the result comes back through a `submit_result` function tool
  (or as a JSON reply), validated against DEEP_RESULT_SCHEMA, with one repair retry;
- refusals and errors return status "failed"; limits return the best answer so far or "timeout".
Web content never appears here, and nothing the model says is acted on except through the validated JSON (INV-7).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from ahbo.agent.prompts import deep_prompt
from ahbo.models import pricing, runtime, sonnet
from ahbo.models.opper import async_client, to_openai_tools

from . import schema
from . import tools as dtools
from .loop import BEST_SO_FAR, FIX_JSON, MAX_SECONDS, MAX_STEPS, MAX_USD, TOOL_TIMEOUT_S, build_brief, submit_tool

log = logging.getLogger("ahbo.deep.compat")

COMPAT_HINT = ("You have no web search on this route: use the provided tools (activities, weather, calendar, "
               "profile). When you have the answer, call submit_result with the JSON object.")


class CompatDeepLoop:
    """Implements `DeepRunner` (CONTRACTS §12) over Opper."""

    def __init__(self, settings: Any, config: Any = None, ctx: dtools.DeepCtx | None = None, *, client: Any = None,
                 max_steps: int = MAX_STEPS, max_seconds: float = MAX_SECONDS, max_usd: float = MAX_USD):
        self.settings, self.config, self.ctx = settings, config, ctx
        self._client = client
        self.max_steps, self.max_seconds, self.max_usd = max_steps, max_seconds, max_usd

    def client(self) -> Any:
        if self._client is None:
            self._client = async_client(self.settings, timeout=60.0)
        return self._client

    def _tools(self) -> list[dict[str, Any]]:
        return to_openai_tools(dtools.TOOLS + [submit_tool()])

    async def run(self, task_id: str, goal: str, constraints: str, context: dict[str, Any], *,
                  purpose: str = "deep_task") -> dict[str, Any]:
        cfg = sonnet.sonnet_cfg(self.config, purpose)
        model = cfg.get("model", "claude-sonnet-5-5")
        t0 = time.monotonic()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": deep_prompt() + "\n" + COMPAT_HINT},
            {"role": "user", "content": build_brief(goal, constraints, context)}]
        spent, steps, json_retry = 0.0, 0, False
        status = "timeout"
        while steps < self.max_steps:
            remaining = self.max_seconds - (time.monotonic() - t0)
            if remaining <= 0:
                break
            steps += 1
            try:
                r = await asyncio.wait_for(self.client().chat.completions.create(
                    model=model, messages=messages, tools=self._tools(), tool_choice="auto",
                    max_tokens=int(cfg.get("max_tokens", 16000))), remaining)
            except asyncio.TimeoutError:
                break
            except Exception:
                log.exception("deep (compat) call failed")
                status = "failed"
                break
            spent += self._record(r, model, purpose, task_id, steps)
            choice = r.choices[0]
            msg = choice.message
            calls = list(msg.tool_calls or [])
            assistant: dict[str, Any] = {"role": "assistant", "content": msg.content}
            if calls:
                assistant["tool_calls"] = [{"id": c.id, "type": "function",
                                            "function": {"name": c.function.name,
                                                         "arguments": c.function.arguments or "{}"}} for c in calls]
            messages.append(assistant)
            submitted = next((c for c in calls if c.function.name == "submit_result"), None)
            if submitted is not None or (not calls and choice.finish_reason in ("stop", "end_turn")):
                obj = self._parse(submitted.function.arguments if submitted else (msg.content or ""))
                if obj is not None and not schema.validate_result({**obj, "task_id": task_id}):
                    return schema.finalize(obj, task_id)
                if json_retry:
                    status = "failed"
                    break
                json_retry = True
                if submitted is not None:
                    messages += [{"role": "tool", "tool_call_id": c.id,
                                  "content": json.dumps({"error": "invalid result" if c is submitted else "skipped"})}
                                 for c in calls]
                messages.append({"role": "user", "content": FIX_JSON})
                continue
            if calls:
                messages += await self._run_tools(calls)
            elif choice.finish_reason == "length":
                messages.append({"role": "user", "content": BEST_SO_FAR})
            else:
                status = "failed"           # content_filter / refusal / unknown
                break
            if spent >= self.max_usd:
                log.warning("deep task %s hit the cost guard ($%.3f)", task_id, spent)
                break
        if status == "timeout" and (time.monotonic() - t0) < self.max_seconds + 15:
            obj = await self._best_so_far(model, messages, task_id)
            if obj is not None:
                return obj
        return schema.failed(task_id, status)

    @staticmethod
    def _parse(text: str) -> dict[str, Any] | None:
        try:
            obj = schema.parse_json_text(text)
        except ValueError:
            return None
        return obj if isinstance(obj, dict) else None

    def _record(self, r: Any, model: str, purpose: str, task_id: str, step: int) -> float:
        u = getattr(r, "usage", None)
        units = {"input_tokens": getattr(u, "prompt_tokens", 0) or 0,
                 "output_tokens": getattr(u, "completion_tokens", 0) or 0} if u is not None else {}
        usd = pricing.usd(model, **units) if units else 0.0
        runtime.record("opper", model, purpose, units, usd, correlation_id=task_id, span="deep.step",
                       attrs={"step": step, "route": "opper"})
        return usd

    async def _run_tools(self, calls: list[Any]) -> list[dict[str, Any]]:
        async def one(c: Any) -> dict[str, Any]:
            try:
                args = json.loads(c.function.arguments or "{}")
            except ValueError:
                args = {}
            if c.function.name not in dtools.NAMES or self.ctx is None:
                res: dict[str, Any] = {"error": "unknown_tool"}
            else:
                try:
                    res = await asyncio.wait_for(dtools.run(self.ctx, c.function.name, args), TOOL_TIMEOUT_S)
                except asyncio.TimeoutError:
                    res = {"error": "timeout"}
            return {"role": "tool", "tool_call_id": c.id, "content": json.dumps(res, ensure_ascii=False)[:20000]}

        return list(await asyncio.gather(*(one(c) for c in calls)))

    async def _best_so_far(self, model: str, messages: list[dict[str, Any]], task_id: str) -> dict[str, Any] | None:
        msgs = list(messages)
        last = msgs[-1] if msgs else {}
        if last.get("role") == "assistant" and last.get("tool_calls"):
            msgs += [{"role": "tool", "tool_call_id": tc["id"], "content": json.dumps({"error": "cut_off"})}
                     for tc in last["tool_calls"]]
        msgs.append({"role": "user", "content": BEST_SO_FAR})
        try:
            r = await asyncio.wait_for(self.client().chat.completions.create(model=model, messages=msgs,
                                                                             max_tokens=4000), 15)
        except Exception:
            return None
        obj = self._parse(r.choices[0].message.content or "")
        if obj is not None and not schema.validate_result({**obj, "task_id": task_id}):
            return schema.finalize(obj, task_id)
        return None
