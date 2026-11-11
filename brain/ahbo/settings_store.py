"""Caregiver settings overlay (owner decision 11 Nov 2026: Telegram is the caregiver + admin interface).

Config-type settings that 阿珍 can change from Telegram live in one kv row, `settings_overlay`, on top of the
YAML defaults (`config/checkins.yaml`). The Kernel reads the *effective* values through this module, never the raw
YAML, so a change applies at once and survives restarts (and Reset demo: the kv table is not reset).

Keys (all "HH:MM" strings): `quiet_start`, `quiet_end`, `morning`, `afternoon`, `evening`, `digest`.
Readers: `kernel.common.quiet_hours` (arbiter, check-ins, turns), `kernel.scheduler.daily_items`
(`checkin.due`, `digest.due`). Writers: `ahbo.links.bot` only (each write is also a `settings.changed` event).
"""
from __future__ import annotations

import re
from typing import Any

KV_KEY = "settings_overlay"
CHECKIN_KINDS = ("morning", "afternoon", "evening")
KEYS = ("quiet_start", "quiet_end", "morning", "afternoon", "evening", "digest")
DEFAULTS = {"quiet_start": "22:30", "quiet_end": "07:00"}
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def valid_hhmm(v: Any) -> bool:
    return isinstance(v, str) and bool(_HHMM.match(v))


def overlay(db: Any) -> dict[str, str]:
    """Only the caregiver's overrides (validated; anything malformed is ignored)."""
    raw = db.kv_get(KV_KEY, {}) if db is not None else {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k in KEYS and valid_hhmm(v)}


def defaults(config: Any) -> dict[str, str]:
    ck = (getattr(config, "checkins", None) or {}) if config is not None else {}
    q = ck.get("quiet_hours") or {}
    out = {"quiet_start": str(q.get("start") or DEFAULTS["quiet_start"]),
           "quiet_end": str(q.get("end") or DEFAULTS["quiet_end"])}
    for k in (*CHECKIN_KINDS, "digest"):
        if ck.get(k):
            out[k] = str(ck[k])
    return out


def effective(db: Any, config: Any) -> dict[str, str]:
    """YAML defaults with the caregiver's overlay on top."""
    return {**defaults(config), **overlay(db)}


def quiet_hours(db: Any, config: Any) -> dict[str, str]:
    e = effective(db, config)
    return {"start": e["quiet_start"], "end": e["quiet_end"]}


def checkin_times(db: Any, config: Any) -> dict[str, str]:
    """`{morning, afternoon, evening, digest}` → "HH:MM" (only the ones that are set)."""
    e = effective(db, config)
    return {k: e[k] for k in (*CHECKIN_KINDS, "digest") if e.get(k)}


def set_values(db: Any, patch: dict[str, str]) -> dict[str, str]:
    """Validate and store overrides; returns the new overlay. Raises ValueError on a bad key/value."""
    for k, v in patch.items():
        if k not in KEYS:
            raise ValueError(f"unknown setting {k!r}")
        if not valid_hhmm(v):
            raise ValueError(f"bad time {v!r}")
    cur = overlay(db)
    cur.update(patch)
    db.kv_set(KV_KEY, cur)
    return cur
