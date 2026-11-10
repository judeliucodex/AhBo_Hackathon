"""Runtime hooks for the model adapters: the DB (knobs in kv `knobs`, costs, spans) and the clock. Set by
`ahbo.agent.wiring.install(svc)`; adapters work without them (no knobs, nothing recorded)."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("ahbo.models")

_db: Any = None
_clock: Any = None


def set_runtime(db: Any = None, clock: Any = None) -> None:
    global _db, _clock
    _db, _clock = db, clock


def knobs() -> dict[str, Any]:
    if _db is None:
        return {}
    try:
        return dict(_db.kv_get("knobs", {}) or {})
    except Exception:
        return {}


def model_cfg(config: Any, section: str, purpose: str | None = None) -> dict[str, Any]:
    """models.yaml section + per-purpose override (§20: nothing hard-coded in code)."""
    if config is None:
        from ahbo.core.config import load_config
        config = load_config()
    sec = dict((config.models or {}).get(section) or {})
    per = (sec.pop("per_purpose", None) or {}).get(purpose or "", {}) or {}
    return {**sec, **per}


def record(provider: str, model: str, purpose: str, usage: dict[str, Any], usd: float, *,
           correlation_id: str | None = None, span: str | None = None, duration_ms: float | None = None,
           status: str = "ok", attrs: dict[str, Any] | None = None) -> None:
    """Cost row + span through `ahbo.trace` when present (lazy import; never raises)."""
    if _db is None:
        return
    try:
        from ahbo.trace.costs import record_cost
        record_cost(_db, provider, model, usage, usd, correlation_id, purpose, clock=_clock)
    except ImportError:
        pass
    except Exception:
        log.exception("cost record failed")
    try:
        from ahbo.trace.spans import record_span
        record_span(_db, span or f"llm.{purpose}", correlation_id=correlation_id, duration_ms=duration_ms,
                    status=status, attrs={"provider": provider, "model": model, "usd": round(usd, 6),
                                          **(attrs or {}), **{k: v for k, v in usage.items() if isinstance(v, (int, float))}},
                    clock=_clock)
    except ImportError:
        pass
    except Exception:
        log.exception("span record failed")
