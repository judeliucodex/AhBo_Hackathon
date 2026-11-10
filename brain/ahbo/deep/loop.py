"""The deep loop (§24.8.5): a hand-written Sonnet 5.5 agent loop, read-only, ≤ 15 steps, ≤ 90 s, ≈ $0.30.

    messages = [user: brief + profile + calendar + date/time]       # system prompt + tools are cached
    for step in 1..15 (stop at 90 s / $0.30):
        msg = stream(...).get_final_message()
        end_turn   → parse + validate the JSON (§7) → return it (one retry on invalid JSON)
        tool_use   → run our read-only tools (timeouts) → ALL tool_results in ONE user message → continue
        pause_turn → the assistant content is appended unchanged → continue
        refusal    → status failed
        max_tokens → one retry asking for the best answer so far as JSON, else status timeout
    on the step/time/cost limit: ask once for the best answer so far, else status timeout

Every assistant `msg.content` is appended back **unchanged** (thinking blocks included, never edited or reordered).
Server-tool errors arrive as HTTP 200 with an error object instead of results; they are detected before anything
indexes into them (`server_tool_errors`). Web text never becomes a tool call or an instruction (INV-7): the only
output is the validated §7 JSON (`deep.schema.finalize`).

`output_config.format` (json_schema) is used by default; if the API rejects it together with server tools, the loop
switches to a strict `submit_result` custom tool, asked for by an instruction, never forced (§20.2 VERIFY note).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from ahbo.agent.prompts import deep_prompt
from ahbo.models import pricing, runtime, sonnet

from . import schema
from . import tools as dtools

log = logging.getLogger("ahbo.deep.loop")

MAX_STEPS = 15
MAX_SECONDS = 90.0
MAX_USD = 0.30
TOOL_TIMEOUT_S = 10.0
BEST_SO_FAR = ("Time is up. Reply now with ONLY the JSON object matching the schema, using the best answer you have "
               "so far (status \"done\" if useful, else \"timeout\").")
FIX_JSON = "Your last reply was not valid JSON for the schema. Reply with ONLY the corrected JSON object."
SUBMIT_HINT = "When you have the answer, call the submit_result tool with the JSON object (do not write it as text)."


def submit_tool() -> dict[str, Any]:
    s = {k: v for k, v in schema.DEEP_RESULT_SCHEMA.items() if k != "$schema"}
    return {"name": "submit_result", "description": "Submit the final result JSON.", "strict": True,
            "input_schema": s}


def block_type(b: Any) -> str:
    return getattr(b, "type", None) or (b.get("type") if isinstance(b, dict) else "") or ""


def server_tool_errors(content: list[Any]) -> list[str]:
    """`web_search_tool_result` / `web_fetch_tool_result` whose content is an error object (HTTP 200)."""
    errs = []
    for b in content or []:
        if block_type(b) in ("web_search_tool_result", "web_fetch_tool_result"):
            c = getattr(b, "content", None) if not isinstance(b, dict) else b.get("content")
            ctype = block_type(c) if c is not None and not isinstance(c, list) else ""
            if ctype.endswith("_error"):
                code = getattr(c, "error_code", None) if not isinstance(c, dict) else c.get("error_code")
                errs.append(f"{block_type(b)}:{code}")
    return errs


def final_text(content: list[Any]) -> str:
    return "".join(getattr(b, "text", "") or "" for b in content or [] if block_type(b) == "text")


def build_brief(goal: str, constraints: str, context: dict[str, Any]) -> str:
    """Volatile data goes in the user message, after the cache breakpoint (§20.2)."""
    parts = [f"Brief (from Ah Bo): {goal}"]
    if constraints:
        parts.append(f"Constraints: {constraints}")
    for k in ("now", "profile", "calendar", "weather"):
        if context.get(k):
            parts.append(f"{k.capitalize()}: {json.dumps(context[k], ensure_ascii=False)[:3000]}")
    parts.append("Return only the JSON result (spoken_summary in colloquial Hong Kong Cantonese, no digits).")
    return "\n".join(parts)


class DeepLoop:
    """Implements `DeepRunner` (CONTRACTS §12)."""

    def __init__(self, settings: Any, config: Any = None, ctx: dtools.DeepCtx | None = None, *, client: Any = None,
                 max_steps: int = MAX_STEPS, max_seconds: float = MAX_SECONDS, max_usd: float = MAX_USD):
        self.settings, self.config, self.ctx = settings, config, ctx
        self._client = client
        self.max_steps, self.max_seconds, self.max_usd = max_steps, max_seconds, max_usd
        self.use_format = True

    def client(self) -> Any:
        if self._client is None:
            self._client = sonnet.make_client(self.settings)
        return self._client

    async def _call(self, kw: dict[str, Any]) -> Any:
        async with self.client().beta.messages.stream(**kw) as stream:
            return await stream.get_final_message()

    def _cost(self, msg: Any, model: str) -> tuple[float, dict[str, Any]]:
        u = getattr(msg, "usage", None)
        if u is None:
            return 0.0, {}
        stu = getattr(u, "server_tool_use", None)
        searches = int(getattr(stu, "web_search_requests", 0) or 0) if stu is not None else 0
        units = {"input_tokens": u.input_tokens or 0, "output_tokens": u.output_tokens or 0,
                 "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
                 "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0, "web_searches": searches}
        return pricing.usd(model, **units), units

    async def run(self, task_id: str, goal: str, constraints: str, context: dict[str, Any], *,
                  purpose: str = "deep_task") -> dict[str, Any]:
        cfg = sonnet.sonnet_cfg(self.config, purpose)
        model = cfg.get("model", "claude-sonnet-5-5")
        t0 = time.monotonic()
        messages: list[dict[str, Any]] = [{"role": "user", "content": build_brief(goal, constraints, context)}]
        spent, steps, json_retry, max_tok_retry = 0.0, 0, False, False
        status = "timeout"
        while steps < self.max_steps:
            remaining = self.max_seconds - (time.monotonic() - t0)
            if remaining <= 0:
                break
            steps += 1
            tools = sonnet.server_tools(cfg) + dtools.TOOLS + ([] if self.use_format else [submit_tool()])
            system = deep_prompt() + ("" if self.use_format else "\n" + SUBMIT_HINT)
            kw = sonnet.request_kwargs(cfg, system=system, tools=tools, messages=messages,
                                       schema=schema.DEEP_RESULT_SCHEMA if self.use_format else None)
            try:
                msg = await asyncio.wait_for(self._call(kw), remaining)
            except asyncio.TimeoutError:
                break
            except Exception as e:
                if self.use_format and _rejects_format(e):
                    log.warning("output_config.format rejected with tools; switching to submit_result")
                    self.use_format = False
                    continue
                log.exception("deep call failed")
                status = "failed"
                break
            usd, units = self._cost(msg, model)
            spent += usd
            runtime.record("anthropic", model, purpose, units, usd, correlation_id=task_id, span="deep.step",
                           attrs={"step": steps, "stop_reason": msg.stop_reason})
            for err in server_tool_errors(msg.content):
                log.info("server tool error (not fatal): %s", err)
            messages.append({"role": "assistant", "content": msg.content})      # unchanged (§20.2)
            sr = msg.stop_reason
            if sr == "end_turn" or (sr == "tool_use" and self._submitted(msg)):
                obj = self._submitted(msg) if sr == "tool_use" else None
                try:
                    obj = obj if obj is not None else schema.parse_json_text(final_text(msg.content))
                except ValueError:
                    obj = None
                if obj is not None and not schema.validate_result({**obj, "task_id": task_id}):
                    return schema.finalize(obj, task_id)
                if json_retry:
                    status = "failed"
                    break
                json_retry = True
                messages.append({"role": "user", "content": FIX_JSON if sr == "end_turn"
                                 else self._tool_results(msg, {"error": "invalid result"})})
                continue
            if sr == "tool_use":
                results = await self._run_tools(msg)
                messages.append({"role": "user", "content": results})
            elif sr == "pause_turn":
                continue                                    # re-send with the assistant content appended
            elif sr == "refusal":
                status = "failed"
                break
            elif sr == "max_tokens":
                if max_tok_retry:
                    status = "timeout"
                    break
                max_tok_retry = True
                pending = [b for b in msg.content or [] if block_type(b) == "tool_use"]
                messages.append({"role": "user", "content": self._tool_results_for(pending, {"error": "cut_off"})
                                 + [{"type": "text", "text": BEST_SO_FAR}] if pending else BEST_SO_FAR})
            else:
                status = "failed"
                break
            if spent >= self.max_usd:
                log.warning("deep task %s hit the cost guard ($%.3f)", task_id, spent)
                break
        # limits reached: one last request for the best answer so far
        if status == "timeout" and (time.monotonic() - t0) < self.max_seconds + 15:
            obj = await self._best_so_far(cfg, messages, task_id)
            if obj is not None:
                return obj
        return schema.failed(task_id, status)

    async def _best_so_far(self, cfg: dict[str, Any], messages: list[dict[str, Any]], task_id: str
                           ) -> dict[str, Any] | None:
        msgs = list(messages)
        if msgs and msgs[-1]["role"] == "assistant":
            last = msgs[-1]["content"]
            pending = [b for b in last or [] if block_type(b) == "tool_use"]
            if pending:
                msgs.append({"role": "user", "content": self._tool_results_for(pending, {"error": "time_up"})
                             + [{"type": "text", "text": BEST_SO_FAR}]})
            else:
                msgs.append({"role": "user", "content": BEST_SO_FAR})
        elif msgs and msgs[-1]["role"] == "user":            # never two user turns in a row: extend the last one
            last = msgs[-1]["content"]
            msgs[-1] = {"role": "user", "content": (list(last) + [{"type": "text", "text": BEST_SO_FAR}])
                        if isinstance(last, list) else f"{last}\n\n{BEST_SO_FAR}"}
        else:
            msgs.append({"role": "user", "content": BEST_SO_FAR})
        kw = sonnet.request_kwargs(cfg, system=deep_prompt(),
                                   tools=sonnet.server_tools(cfg) + dtools.TOOLS, messages=msgs,
                                   schema=schema.DEEP_RESULT_SCHEMA if self.use_format else None)
        try:
            msg = await asyncio.wait_for(self._call(kw), 15.0)
            obj = schema.parse_json_text(final_text(msg.content))
        except Exception:
            return None
        if schema.validate_result({**obj, "task_id": task_id}):
            return None
        return schema.finalize(obj, task_id)

    @staticmethod
    def _submitted(msg: Any) -> dict[str, Any] | None:
        for b in msg.content or []:
            if block_type(b) == "tool_use" and getattr(b, "name", "") == "submit_result":
                inp = getattr(b, "input", None)
                return dict(inp) if isinstance(inp, dict) else None
        return None

    @staticmethod
    def _tool_results_for(blocks: list[Any], result: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(result, ensure_ascii=False),
                 "is_error": True} for b in blocks]

    def _tool_results(self, msg: Any, result: dict[str, Any]) -> list[dict[str, Any]]:
        return self._tool_results_for([b for b in msg.content if block_type(b) == "tool_use"], result)

    async def _run_tools(self, msg: Any) -> list[dict[str, Any]]:
        """Our custom read-only tools only (server tools run on Anthropic's side). ALL results in ONE message."""
        uses = [b for b in msg.content if block_type(b) == "tool_use"]

        async def one(b: Any) -> dict[str, Any]:
            if b.name not in dtools.NAMES or self.ctx is None:
                res: dict[str, Any] = {"error": "unknown_tool"}
            else:
                try:
                    res = await asyncio.wait_for(dtools.run(self.ctx, b.name, dict(b.input or {})), TOOL_TIMEOUT_S)
                except asyncio.TimeoutError:
                    res = {"error": "timeout"}
            out = {"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(res, ensure_ascii=False)[:20000]}
            if "error" in res:
                out["is_error"] = True
            return out

        return list(await asyncio.gather(*(one(b) for b in uses)))


def _rejects_format(e: Exception) -> bool:
    status = getattr(e, "status_code", None)
    text = str(e).lower()
    return status == 400 and ("output_config" in text or "format" in text or "json_schema" in text)
