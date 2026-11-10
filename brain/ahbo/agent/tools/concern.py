"""Concern and incident tools (§4.3.4, INV-2). raise_concern may raise (never lower) a concern; the Kernel's
red-flag matcher may raise the level further. get_incident_status is the only tool besides get_time allowed in
`reassure` mode, and only there."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from . import UNAVAILABLE, ToolCtx, kernel_request, tool


class ConcernArgs(BaseModel):
    model_config = {"extra": "ignore"}
    level: Literal["info", "urgent", "emergency"]
    category: str = Field("other", max_length=30)
    reason: str = Field("", max_length=300)


@tool("raise_concern", "佢講身體唔舒服、痛、跌親、情緒好差、或者疑似俾人呃錢嗰陣用（category 例如 symptom、scam、mood）。",
      ConcernArgs)
async def raise_concern(ctx: ToolCtx, args: ConcernArgs) -> dict[str, Any]:
    res = await kernel_request(ctx, "agent.concern", level=args.level, category=args.category.strip() or "other",
                               reason=args.reason.strip())
    return res or dict(UNAVAILABLE)


@tool("get_incident_status", "而家意外處理去到邊（只係安撫模式用）。照住 words 平靜咁講，唔好加承諾。",
      modes=("reassure",))
async def get_incident_status(ctx: ToolCtx, _args) -> dict[str, Any]:
    res = await kernel_request(ctx, "incident.status_request")
    return res or dict(UNAVAILABLE)
