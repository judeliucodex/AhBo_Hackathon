"""Tool layer (§5.5, CONTRACTS §6). Every tool executes inside `api` (the single writer); the LiveKit worker
reaches them through POST /internal/tool/{name}, the text loop calls them in-process. Same code either way.

A tool is `async def fn(ctx: ToolCtx, args: ArgsModel) -> dict`. Its result always has `ok`. Two optional
private keys are stripped before the result reaches the LLM:
  `_cards`: kit card objects for the turn (shown when the reply starts, §23.7), each may carry
            `when_candidates` (phrases that may appear in the reply);
  `_step`:  `{"outcome": <ui_steps done key | "failed" | "pending">, "vars": {...}}` to fill the step label;
  `_tone`:  `family` | `propose` (reply mood hint, §23.3).
"""
from __future__ import annotations

import importlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from ahbo.kernel.core import Kernel

log = logging.getLogger("ahbo.tools")

ALL_MODES = ("companion", "reminder")
# Mode filter (CONTRACTS §6): kernel_listen and paused allow nothing; reassure allows get_time and
# get_incident_status only. A tool lists the modes it may run in.
REASSURE_OK = ALL_MODES + ("reassure",)
PRIVATE_KEYS = ("_cards", "_step", "_tone")


class NoArgs(BaseModel):
    model_config = {"extra": "ignore"}


@dataclass
class ToolCtx:
    kernel: Kernel
    session_id: str | None = None
    turn_id: str | None = None
    call_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def db(self):
        return self.kernel.db

    @property
    def clock(self):
        return self.kernel.clock

    @property
    def config(self):
        return self.kernel.config

    @property
    def settings(self):
        return self.kernel.settings

    @property
    def state(self):
        return self.kernel.state

    @property
    def mode(self) -> str:
        st = self.kernel.state
        if st.session is not None:
            return st.session.mode
        if st.incident_active:      # no session during an incident: silent before escalation, calm after it
            inc = st.incident
            state = inc.get("state") if isinstance(inc, dict) else getattr(inc, "state", None)
            return "reassure" if state in ("ESCALATING", "REASSURING") else "kernel_listen"
        return "companion"


ToolFn = Callable[[ToolCtx, Any], Awaitable[dict[str, Any]]]


@dataclass
class ToolDef:
    name: str
    args: type[BaseModel]
    fn: ToolFn
    description: str
    modes: tuple[str, ...] = ALL_MODES
    parameters: dict[str, Any] | None = None   # JSON Schema for the LLM (defaults to the Pydantic schema)

    def schema(self) -> dict[str, Any]:
        params = self.parameters or self.args.model_json_schema()
        params = {k: v for k, v in params.items() if k != "title"}
        for p in params.get("properties", {}).values():
            p.pop("title", None)
        params.setdefault("type", "object")
        params.setdefault("properties", {})
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": params}}


TOOLS: dict[str, ToolDef] = {}

TOOL_MODULES = ("quick", "meds", "memory", "deep", "actions", "family", "concern", "cards")


def tool(name: str, description: str, args: type[BaseModel] = NoArgs, modes: tuple[str, ...] = ALL_MODES,
         parameters: dict[str, Any] | None = None):
    def deco(fn: ToolFn) -> ToolFn:
        TOOLS[name] = ToolDef(name, args, fn, description, modes, parameters)
        return fn
    return deco


def load_tools() -> dict[str, ToolDef]:
    for m in TOOL_MODULES:
        try:
            importlib.import_module(f"{__name__}.{m}")
        except ModuleNotFoundError as e:
            if e.name and e.name.endswith(f"tools.{m}"):
                continue
            raise
    return TOOLS


def schemas(mode: str = "companion") -> list[dict[str, Any]]:
    load_tools()
    return [t.schema() for t in TOOLS.values() if mode in t.modes]


async def run_tool(ctx: ToolCtx, name: str, raw_args: dict[str, Any] | None) -> dict[str, Any]:
    """Validate and run one tool. Never raises: errors become {ok: false, reason}."""
    load_tools()
    td = TOOLS.get(name)
    if td is None:
        return {"ok": False, "reason": "unknown_tool"}
    if ctx.mode not in td.modes:
        return {"ok": False, "reason": "mode"}
    try:
        args = td.args.model_validate(raw_args or {})
    except ValidationError:
        return {"ok": False, "reason": "bad_args"}
    try:
        res = await td.fn(ctx, args)
    except Exception as e:  # a tool bug must not crash the turn
        log.exception("tool %s failed", name)
        return {"ok": False, "reason": "tool_error", "_detail": repr(e)[:200]}
    if not isinstance(res, dict) or "ok" not in res:
        return {"ok": False, "reason": "tool_error"}
    return res


UNAVAILABLE: dict[str, Any] = {"ok": False, "reason": "unavailable"}


async def kernel_request(ctx: ToolCtx, type_: str, **payload: Any) -> dict[str, Any] | None:
    """Ask the Kernel (a reducer answers with ctx.reply, AGENT_BRIEF request table). Returns None when no reducer
    answered (that flow is not installed); callers then return UNAVAILABLE."""
    from ahbo.core.events import Event
    res = await ctx.kernel.request(Event(type=type_, source="agent", correlation_id=ctx.session_id,
                                         payload={"session_id": ctx.session_id, "turn_id": ctx.turn_id,
                                                  "call_id": ctx.call_id, **payload}))
    return dict(res) if isinstance(res, dict) else None


def public(result: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in result.items() if k not in PRIVATE_KEYS and not k.startswith("_")}
