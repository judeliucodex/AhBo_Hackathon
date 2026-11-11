"""Per-HKT-day spend caps (§11, `config/caps.yaml`). Exceeding a cap disables that feature with a console warning.

    ok, used, cap = check_cap(db, clock, config, "llm_usd")

Names: `llm_usd`, `deep_tasks`, `twilio_calls`, `playwright_actions`, `sessions_per_day`, `session_minutes`
(per session: pass `session_id`). Twilio safety calls (pass `safety=True, incident_id=…`) are exempt up to 3 per
incident (§11); beyond that they count like any other call. `check_cap` is read-only apart from the warning:
the feature records its usage with `trace.costs.record_cost` after it acts.

The warning is logged once per cap per day, stored in `quality` (`cap_exceeded`), and, when a notifier is set
(`trace.wiring.install` sets one that submits a `system.cap_exceeded` Kernel event), sent to the console.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ahbo.core.clock import Clock
from ahbo.core.db import Database
from ahbo.core.ids import new_id
from ahbo.core.jsonutil import dumps
from ahbo.trace.costs import calls_for_incident, spend_today

log = logging.getLogger("ahbo.caps")

SAFETY_CALLS_PER_INCIDENT = 3
CAP_NAMES = ("llm_usd", "deep_tasks", "twilio_calls", "playwright_actions", "sessions_per_day", "session_minutes")
_USAGE_KEY = {"llm_usd": "llm_usd", "deep_tasks": "deep_tasks", "twilio_calls": "twilio_calls",
              "playwright_actions": "playwright_actions", "sessions_per_day": "sessions"}

_notifier: Callable[[dict[str, Any]], None] | None = None


def set_notifier(fn: Callable[[dict[str, Any]], None] | None) -> None:
    global _notifier
    _notifier = fn


def _caps(config: Any) -> dict[str, Any]:
    return getattr(config, "caps", None) or (config if isinstance(config, dict) else {})


def _session_minutes(db: Database, clock: Clock, session_id: str | None) -> float:
    if not session_id:
        return 0.0
    r = db.one("SELECT opened_at, closed_at FROM sessions WHERE id=?", (session_id,))
    if not r:
        return 0.0
    start = datetime.fromisoformat(r["opened_at"])
    end = datetime.fromisoformat(r["closed_at"]) if r.get("closed_at") else clock.now()
    return round(max(0.0, (end - start).total_seconds() / 60), 2)


def _warn(db: Database, clock: Clock, name: str, used: float, cap: float) -> None:
    day = clock.today().isoformat()
    key = f"cap_warned:{day}:{name}"
    if db.kv_get(key):
        return
    db.kv_set(key, True)
    detail = {"name": name, "used": used, "cap": cap, "date": day}
    log.warning("spend cap reached: %s used=%s cap=%s; feature disabled for today", name, used, cap)
    try:
        db.execute("INSERT INTO quality(id, ts, kind, detail, correlation_id) VALUES (?,?,?,?,?)",
                   (new_id("q"), clock.iso(), "cap_exceeded", dumps(detail), None))
    except Exception:
        log.exception("could not record cap warning")
    if _notifier:
        try:
            _notifier(detail)
        except Exception:
            log.exception("cap notifier failed")


def check_cap(db: Database, clock: Clock, config: Any, name: str, *, session_id: str | None = None,
              safety: bool = False, incident_id: str | None = None, adding: float = 1) -> tuple[bool, float, float]:
    """(ok, used, cap). `ok` means one more unit (`adding`; dollars are checked as used < cap) is allowed."""
    if name not in CAP_NAMES:
        raise ValueError(f"unknown cap {name!r}")
    caps = _caps(config)
    cap = float(caps.get(name, 0) or 0)
    if name == "session_minutes":
        used = _session_minutes(db, clock, session_id)
        ok = cap <= 0 or used < cap
    else:
        used = spend_today(db, clock)[_USAGE_KEY[name]]
        if (name == "twilio_calls" and safety and incident_id
                and calls_for_incident(db, incident_id) < SAFETY_CALLS_PER_INCIDENT):
            return True, used, cap              # exempt (§11)
        if cap <= 0:
            ok = True                           # 0 / missing = no cap configured
        elif name == "llm_usd":
            ok = used < cap
        else:
            ok = used + adding <= cap
    if not ok:
        _warn(db, clock, name, used, cap)
    return ok, used, cap


def all_caps(db: Database, clock: Clock, config: Any) -> dict[str, dict[str, Any]]:
    """For the console: {name: {used, cap, ok}} for the daily caps."""
    caps = _caps(config)
    usage = spend_today(db, clock)
    out = {}
    for name, key in _USAGE_KEY.items():
        cap = float(caps.get(name, 0) or 0)
        used = usage[key]
        out[name] = {"used": used, "cap": cap, "ok": cap <= 0 or (used < cap)}
    out["session_minutes"] = {"used": None, "cap": float(caps.get("session_minutes", 0) or 0), "ok": True}
    return out
