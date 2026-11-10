"""External actions (§4.3.5): request_action proposes (PROPOSED, nothing runs), confirm_action asks the Kernel to
start the runner after her clear yes. Both only *ask* (`action.propose_request` / `action.confirm_request`); the
action reducer checks the proposal and the STT confidence (INV-1)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from . import UNAVAILABLE, ToolCtx, kernel_request, tool


class RequestArgs(BaseModel):
    model_config = {"extra": "ignore"}
    kind: Literal["signup", "message"]
    target: str = Field(min_length=1, max_length=80)
    details: dict[str, Any] = Field(default_factory=dict)


@tool("request_action", "提議一個要出街做嘅行動（例如幫佢報名）。只係提議，唔會做；之後要問佢一條清楚嘅好唔好問題。",
      RequestArgs)
async def request_action(ctx: ToolCtx, args: RequestArgs) -> dict[str, Any]:
    res = await kernel_request(ctx, "action.propose_request", kind=args.kind, target=args.target,
                               details=args.details)
    return res or dict(UNAVAILABLE)


class ConfirmActionArgs(BaseModel):
    model_config = {"extra": "ignore"}
    action_id: str = Field(min_length=1, max_length=64)
    utterance: str = Field("", max_length=200)
    stt_confidence: float | None = Field(None, ge=0, le=1)


def _last_stt_confidence(ctx: ToolCtx) -> float | None:
    if not ctx.session_id:
        return None
    row = ctx.db.one("SELECT stt_confidence FROM turns WHERE session_id=? AND role='user' ORDER BY ts DESC, rowid DESC "
                     "LIMIT 1", (ctx.session_id,))
    v = (row or {}).get("stt_confidence")
    return float(v) if v is not None else None


@tool("confirm_action", "佢清楚話好之後，先用呢個開始做之前提議咗嘅行動（utterance 係佢講嘅原話）。", ConfirmActionArgs)
async def confirm_action(ctx: ToolCtx, args: ConfirmActionArgs) -> dict[str, Any]:
    conf = args.stt_confidence if args.stt_confidence is not None else _last_stt_confidence(ctx)
    payload: dict[str, Any] = {"action_id": args.action_id, "utterance": args.utterance}
    if conf is not None:
        payload["stt_confidence"] = conf
    res = await kernel_request(ctx, "action.confirm_request", **payload)
    if res is None:
        return {**UNAVAILABLE, "_step": {"outcome": "failed", "vars": {}}}
    # running: the step stays active until `action.status` (the Kernel finishes it)
    return {**res, "_step": {"outcome": "pending" if res.get("ok") else "failed", "vars": {}}}
