"""Small helpers shared by the flow reducers (no I/O except read-only DB queries and Kernel-internal records)."""
from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Any

from ahbo.core.events import Event
from ahbo.core.jsonutil import loads
from ahbo.core.settings import EMERGENCY_NUMBERS

FAKE_SECONDS_PER_CHAR = 0.19          # max wait for a fixed clip when no playback_done arrives
CALLABLE_ROLES = ("emergency_centre_sim", "family")   # INV-9: the Kernel only ever asks to call these


def quiet_hours(ctx: Any, at: datetime | None = None) -> bool:
    q = ctx.config.checkins.get("quiet_hours") or {}
    start, end = q.get("start", "22:30"), q.get("end", "07:00")
    hm = (at or ctx.now).strftime("%H:%M")
    return (hm >= start or hm < end) if start > end else (start <= hm < end)


def clip_text(ctx: Any, clip_id: str) -> str:
    txt = ctx.config.fixed_lines.get(clip_id)
    if txt:
        return txt
    if clip_id.startswith("R_"):
        meds = dose_med_names(ctx, clip_id[2:])
        return (ctx.config.fixed_lines.get("R_TEMPLATE") or "{med_names}").replace("{med_names}", "、".join(meds))
    return ""


def clip_max(ctx: Any, clip_id: str, floor: float = 1.0) -> float:
    """Seconds the Kernel waits for a clip's playback_done before moving on (§10.4: never hang on the iPad)."""
    return max(floor, len(clip_text(ctx, clip_id)) * FAKE_SECONDS_PER_CHAR) + 0.3


def dose_med_names(ctx: Any, slot_id: str) -> list[str]:
    row = ctx.db.one("SELECT med_names FROM med_schedule WHERE slot_id=?", (slot_id,))
    ids = loads(row["med_names"], []) if row else []
    cat = {r["id"]: r["yue"] for r in ctx.db.query("SELECT id, yue FROM med_catalogue")}
    return [cat[i] for i in ids if i in cat]


def note(ctx: Any, type_: str, payload: dict[str, Any], corr: str | None = None) -> None:
    """Record an informational event now (e.g. `arbiter.decision`), without reducers."""
    ctx.kernel.note(Event(type=type_, source="system", correlation_id=corr, payload=payload))


def later(ctx: Any, type_: str, payload: dict[str, Any], corr: str | None = None, source: str = "system") -> None:
    """Queue an event for the Kernel (processed after the current one), e.g. `plan.due` from operator.plan_now."""
    ctx.kernel.submit_nowait(Event(type=type_, source=source, correlation_id=corr, payload=payload))


# ---------------------------------------------------------------- INV-9: who the Kernel may ask to call
def phone_digits(phone: str | None) -> str:
    out = []
    for ch in str(phone or ""):
        if ch.isdigit():
            try:
                out.append(str(unicodedata.digit(ch)))
            except (TypeError, ValueError):
                out.append(ch)
    return "".join(out)


def is_emergency_number(phone: str | None) -> bool:
    """999/112/911 in any formatting (+852 999, (911), full-width ９９９, 00852-999 …) or any short code."""
    if phone is None or str(phone).strip() == "":
        return False
    d = phone_digits(phone)
    if not d or len(d) < 8:
        return True                      # short codes and partial numbers are never dialled
    return any(d == n or (d.endswith(n) and len(d) - len(n) <= 5) for n in EMERGENCY_NUMBERS)


def callable_contact(c: dict[str, Any] | None) -> bool:
    return bool(c) and int(c.get("verified") or 0) == 1 and c.get("role") in CALLABLE_ROLES \
        and not is_emergency_number(c.get("phone"))


def call_order(ctx: Any) -> list[dict[str, Any]]:
    """Who the fall flow phones (owner decision 11 Nov): the caregivers only (role family), by priority. The simulated
    emergency centre is no longer called automatically."""
    rows = ctx.db.query("SELECT * FROM contacts WHERE verified=1 AND role='family' ORDER BY priority, id")
    return [r for r in rows if callable_contact(r)]


def family_contact_ids(ctx: Any) -> list[str]:
    return [r["id"] for r in ctx.db.query("SELECT id FROM contacts WHERE role='family' ORDER BY priority, id")]


def address(ctx: Any) -> str:
    r = ctx.db.one("SELECT address FROM resident LIMIT 1")
    return (r or {}).get("address") or ""
