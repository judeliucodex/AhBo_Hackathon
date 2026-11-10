"""The deep agent's custom tools (§24.8.1): read-only, strict schemas, each with a timeout in the loop.
Web search and web fetch are Anthropic server tools (§20.2), not here. Nothing here writes anything (INV-1)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from ahbo.core.jsonutil import loads

log = logging.getLogger("ahbo.deep.tools")


@dataclass
class DeepCtx:
    db: Any
    clock: Any
    settings: Any


def _tool(name: str, description: str, props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"name": name, "description": description, "strict": True,
            "input_schema": {"type": "object", "additionalProperties": False, "properties": props,
                             "required": required if required is not None else list(props)}}


TOOLS: list[dict[str, Any]] = [
    _tool("get_weather_forecast", "Hong Kong Observatory 9-day forecast (date, weather, min/max °C).", {}),
    _tool("read_calendar", "Her calendar for the next N days (default 7), including medicine times as fixed blocks.",
          {"days": {"type": "integer", "description": "1-14"}}),
    _tool("read_profile", "Her stable profile facts (preferences, mobility, family) and home district.", {}),
    _tool("find_local_activities", "Curated nearby elderly activities (seed list) filtered by interest.",
          {"interest": {"type": "string", "description": "e.g. 粵曲, 運動, 手工; empty for all"}}),
]
NAMES = {t["name"] for t in TOOLS}


async def get_weather_forecast(ctx: DeepCtx, _args: dict[str, Any]) -> dict[str, Any]:
    from ahbo.agent.tools import weather
    return {"days": await weather.forecast_9day(ctx.settings), "now": await weather.current(ctx.settings)}


async def read_calendar(ctx: DeepCtx, args: dict[str, Any]) -> dict[str, Any]:
    days = max(1, min(14, int(args.get("days") or 7)))
    now = ctx.clock.now()
    end = (now + timedelta(days=days)).date().isoformat()
    evs = ctx.db.query("SELECT datetime, title, place, kind, status FROM calendar_events WHERE status!='cancelled' "
                       "AND substr(datetime,1,10)>=? AND substr(datetime,1,10)<=? ORDER BY datetime",
                       (now.date().isoformat(), end))
    meds = [{"time": r["time"], "block": "medicine (fixed; do not schedule over it)"}
            for r in ctx.db.query("SELECT time FROM med_schedule WHERE active=1 ORDER BY time")]
    return {"today": now.date().isoformat(), "now": now.isoformat(), "events": evs, "daily_fixed_blocks": meds}


async def read_profile(ctx: DeepCtx, _args: dict[str, Any]) -> dict[str, Any]:
    r = ctx.db.one("SELECT name_zh, age, district, shareable FROM resident LIMIT 1") or {}
    facts = [f["fact"] for f in ctx.db.query("SELECT fact FROM facts WHERE status='active' AND category!='health' "
                                              "ORDER BY importance DESC LIMIT 20")]
    sh = loads(r.get("shareable"), {}) or {}
    return {"name": r.get("name_zh"), "age": r.get("age"), "district": r.get("district") or sh.get("district"),
            "language": sh.get("language", "廣東話"), "facts": facts}


async def find_local_activities(ctx: DeepCtx, args: dict[str, Any]) -> dict[str, Any]:
    from ahbo.agent.tools.quick import activities
    interest = (args.get("interest") or "").strip()
    out = []
    for a in activities():
        if interest and not any(interest in i or i in interest for i in a.get("interests", []) + [a["title"]]):
            continue
        out.append({k: a.get(k) for k in ("id", "title", "venue", "time", "days", "walk_minutes", "interests", "signup")})
    return {"activities": out}


IMPL = {"get_weather_forecast": get_weather_forecast, "read_calendar": read_calendar, "read_profile": read_profile,
        "find_local_activities": find_local_activities}


async def run(ctx: DeepCtx, name: str, args: dict[str, Any]) -> dict[str, Any]:
    fn = IMPL.get(name)
    if fn is None:
        return {"error": "unknown_tool"}
    try:
        return await fn(ctx, args or {})
    except Exception as e:
        log.exception("deep tool %s failed", name)
        return {"error": type(e).__name__}
