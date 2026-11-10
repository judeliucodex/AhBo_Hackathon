"""deep_task (§24.8.3): start background research/planning. Returns at once; the result arrives later as
`deep.result` and is injected by the Kernel (`背景結果`). The step stays active until then (`outcome: pending`)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from . import UNAVAILABLE, ToolCtx, kernel_request, tool

DESCRIPTION = ("Start background research or planning. Returns immediately; the result arrives later. "
               "用嚟計劃一日/一個星期、推介種子活動以外嘅地方、比較選擇、或者要上網查新資料。")


class DeepArgs(BaseModel):
    model_config = {"extra": "ignore"}
    goal: str = Field(min_length=1, max_length=300)
    constraints: str = Field("", max_length=500)


PARAMS = {"type": "object", "properties": {
    "goal": {"type": "string", "description": "What she wants, in one sentence"},
    "constraints": {"type": "string", "description": "Time, place, mobility, budget, preferences from the conversation"}},
    "required": ["goal"]}


@tool("deep_task", DESCRIPTION, DeepArgs, parameters=PARAMS)
async def deep_task(ctx: ToolCtx, args: DeepArgs) -> dict[str, Any]:
    res = await kernel_request(ctx, "deep.start_request", goal=args.goal.strip(), constraints=args.constraints.strip())
    if res is None:
        return {**UNAVAILABLE, "_step": {"outcome": "failed", "vars": {}}}
    if res.get("ok"):
        return {**res, "_step": {"outcome": "pending", "vars": {}}}
    res.setdefault("status", "busy")
    return {**res, "_step": {"outcome": "pending", "vars": {}}}
