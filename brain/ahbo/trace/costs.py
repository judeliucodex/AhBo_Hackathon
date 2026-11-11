"""Costs (§15): one row per billable call in `costs`; spend caps read from here (§11, `ops.caps`).

Counting rules (so fakes never use up a cap: fake adapters record with provider `fake`, or not at all):
- `llm_usd`: SUM(usd) for providers deepseek / anthropic / opper / openai.
- `deep_tasks`: distinct correlation ids with purpose `deep_task`.
- `twilio_calls`: rows with provider `twilio` (purpose `call`, or `safety_call` for fall/SOS escalations).
- `playwright_actions`: rows with provider `playwright`.
- `sessions`: rows of `sessions` opened today (fake or real; a runaway guard, not money).
"Today" is the virtual clock's HKT date; timestamps are ISO strings from the same clock.
"""
from __future__ import annotations

from typing import Any

from ahbo.core.clock import Clock
from ahbo.core.db import Database
from ahbo.core.ids import new_id
from ahbo.core.jsonutil import dumps

LLM_PROVIDERS = ("deepseek", "anthropic", "opper", "openai")


def record_cost(db: Database, provider: str, model: str | None, units: dict[str, Any] | None, usd: float,
                correlation_id: str | None = None, purpose: str | None = None, *, clock: Clock | None = None) -> str:
    from ahbo.core.clock import get_clock

    cid = new_id("cost")
    db.execute("INSERT INTO costs(id, ts, provider, model, purpose, units, usd, correlation_id) VALUES (?,?,?,?,?,?,?,?)",
               (cid, (clock or get_clock()).iso(), provider, model, purpose, dumps(units or {}), float(usd or 0),
                correlation_id))
    return cid


def _day(clock: Clock) -> str:
    return clock.today().isoformat()


def spend_today(db: Database, clock: Clock) -> dict[str, Any]:
    """Today's usage: {llm_usd, total_usd, deep_tasks, twilio_calls, playwright_actions, sessions}."""
    d = _day(clock)
    q = lambda sql, *p: (db.one(sql, (d, *p)) or {}).get("v") or 0
    ph = ",".join("?" * len(LLM_PROVIDERS))
    return {
        "llm_usd": round(float(q(f"SELECT SUM(usd) v FROM costs WHERE substr(ts,1,10)=? AND provider IN ({ph})",
                                 *LLM_PROVIDERS)), 4),
        "total_usd": round(float(q("SELECT SUM(usd) v FROM costs WHERE substr(ts,1,10)=?")), 4),
        "deep_tasks": int(q("SELECT COUNT(DISTINCT COALESCE(correlation_id, id)) v FROM costs "
                            "WHERE substr(ts,1,10)=? AND purpose='deep_task'")),
        "twilio_calls": int(q("SELECT COUNT(*) v FROM costs WHERE substr(ts,1,10)=? AND provider='twilio'")),
        "playwright_actions": int(q("SELECT COUNT(*) v FROM costs WHERE substr(ts,1,10)=? AND provider='playwright'")),
        "sessions": int(q("SELECT COUNT(*) v FROM sessions WHERE substr(opened_at,1,10)=?")),
    }


def calls_for_incident(db: Database, incident_id: str) -> int:
    r = db.one("SELECT COUNT(*) v FROM costs WHERE provider='twilio' AND purpose='safety_call' AND correlation_id=?",
               (incident_id,))
    return int((r or {}).get("v") or 0)


def daily_totals(db: Database, days: int = 7) -> list[dict[str, Any]]:
    """Per-day USD by provider for the console (newest first)."""
    return db.query("SELECT substr(ts,1,10) AS day, provider, ROUND(SUM(usd), 4) AS usd, COUNT(*) AS calls "
                    "FROM costs GROUP BY day, provider ORDER BY day DESC, provider LIMIT ?", (days * 10,))
