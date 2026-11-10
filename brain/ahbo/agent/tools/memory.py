"""Memory tools (§5.5, §7): recall, remember, recall_life_story. Thin wrappers over `ahbo.memory`
(FULL_GUIDE §5): recall → `store.recall(db, q, 5, clock=)`; remember → `store.remember` (MemoryRejected → ok:false
with its reason); recall_life_story → `pick_life_story` + `mark_story_used` + a `story` card."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from . import ToolCtx, tool


class RecallArgs(BaseModel):
    model_config = {"extra": "ignore"}
    query: str = Field(min_length=1, max_length=200)


@tool("recall", "喺記憶度搵佢講過嘅嘢、屋企人資料同以前傾過嘅偈（例如琴日講咗咩）。", RecallArgs)
async def recall(ctx: ToolCtx, args: RecallArgs) -> dict[str, Any]:
    from ahbo.memory import store
    r = store.recall(ctx.db, args.query, 5, clock=ctx.clock)
    return {"ok": True, "facts": r.get("facts", [])[:5], "episodes": r.get("episodes", [])[:5]}


class RememberArgs(BaseModel):
    model_config = {"extra": "ignore"}
    fact: str = Field(min_length=1, max_length=200)
    category: str = Field("other", max_length=20)   # normalised by the store (unknown → other)


@tool("remember", "記住一樣佢叫你記住、或者好清楚又唔會變嘅事實。唔好記密碼、身份證或者銀行資料。", RememberArgs)
async def remember(ctx: ToolCtx, args: RememberArgs) -> dict[str, Any]:
    from ahbo.memory import store
    try:
        fid = store.remember(ctx.db, ctx.clock, args.fact, args.category, source="voice")
    except store.MemoryRejected as e:
        return {"ok": False, "reason": e.reason}
    row = ctx.db.one("SELECT status FROM facts WHERE id=?", (fid,)) or {}
    return {"ok": True, "id": fid, "status": row.get("status", "active")}


class StoryArgs(BaseModel):
    model_config = {"extra": "ignore"}
    topic: str | None = Field(None, max_length=40)


@tool("recall_life_story", "揀一個王婆婆以前嘅生活小故事同佢傾（可以畀個題目）。", StoryArgs)
async def recall_life_story(ctx: ToolCtx, args: StoryArgs) -> dict[str, Any]:
    from ahbo.memory import life_stories
    s = life_stories.pick_life_story(ctx.db, ctx.clock, args.topic or None)
    if s is None:
        return {"ok": True, "story": None}
    life_stories.mark_story_used(ctx.db, ctx.clock, s["id"])
    story = {k: s.get(k) for k in ("id", "title", "text", "era")}
    if s.get("photo_url"):
        story["photo_url"] = s["photo_url"]
    card: dict[str, Any] = {"id": f"story_{s['id']}", "kind": "story", "label": s["title"],
                            "small": s.get("era"), "when_candidates": [s["title"]]}
    if s.get("photo_url"):
        card["photo"] = s["photo_url"]
    card = {k: v for k, v in card.items() if v is not None}
    return {"ok": True, "story": story, "_cards": [card]}

