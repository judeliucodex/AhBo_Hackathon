"""`GET /operator/api/state` snapshot (CONTRACTS §9) and the health table fallback."""
from __future__ import annotations

import dataclasses
import shutil
from datetime import datetime
from typing import Any

from ahbo.core.jsonutil import dumps, loads

from .services import Services, read_manifest


def _plain(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        obj = dataclasses.asdict(obj)
    return loads(dumps(obj))


def _incident(svc: Services) -> dict[str, Any] | None:
    inc = svc.kernel.state.incident
    if inc is not None:
        d = _plain(inc)
        if isinstance(d, dict) and d.get("id"):
            row = svc.db.one("SELECT * FROM incidents WHERE id=?", (d["id"],)) or {}
            return {"id": d.get("id"), "kind": d.get("kind") or row.get("kind"),
                    "state": d.get("state") or row.get("state"), "source": d.get("source") or row.get("source"),
                    "opened_at": d.get("opened_at") or row.get("opened_at"),
                    "timeline": loads(row.get("timeline"), []) if row else d.get("timeline", []),
                    "vision_note": row.get("vision_note") or d.get("vision_note")}
    row = svc.db.one("SELECT * FROM incidents WHERE closed_at IS NULL ORDER BY opened_at DESC LIMIT 1")
    if not row:
        return None
    return {"id": row["id"], "kind": row["kind"], "state": row["state"], "source": row["source"],
            "opened_at": row["opened_at"], "timeline": loads(row["timeline"], []), "vision_note": row["vision_note"]}


def _doses(svc: Services) -> list[dict[str, Any]]:
    today = svc.clock.today().isoformat()
    events = {r["slot_id"]: r for r in svc.db.query("SELECT * FROM dose_events WHERE date=?", (today,))}
    out = []
    for s in svc.db.query("SELECT * FROM med_schedule WHERE active=1 ORDER BY time"):
        ev = events.get(s["slot_id"]) or {}
        out.append({"slot_id": s["slot_id"], "time": s["time"], "state": ev.get("state", "scheduled"),
                    "meds": loads(s["med_names"], []), "snoozes": ev.get("snoozes", 0)})
    return out


def _spend(svc: Services) -> dict[str, Any]:
    day = svc.clock.today().isoformat()
    like = day + "%"
    db = svc.db
    usd = db.one("SELECT COALESCE(SUM(usd),0) AS v FROM costs WHERE ts LIKE ?", (like,))["v"]
    return {"llm_usd": round(float(usd or 0), 4),
            "deep_tasks": db.one("SELECT COUNT(*) AS n FROM deep_tasks WHERE started_at LIKE ?", (like,))["n"],
            "twilio_calls": db.one("SELECT COUNT(*) AS n FROM outbox WHERE channel='twilio' AND ts LIKE ?",
                                   (like,))["n"],
            "playwright_actions": db.one("SELECT COUNT(*) AS n FROM actions WHERE created_at LIKE ?", (like,))["n"],
            "sessions": db.one("SELECT COUNT(*) AS n FROM sessions WHERE opened_at LIKE ? AND reason != 'seed'",
                               (like,))["n"]}


def state_snapshot(svc: Services) -> dict[str, Any]:
    k = svc.kernel
    st = k.state
    s = st.session
    session = None if s is None else {"id": s.id, "mode": s.mode, "reason": s.reason, "text_only": s.text_only,
                                      "agent_state": s.agent_state, "opened_at": s.opened_at.isoformat()}
    knobs = {"flash_thinking": "off", "sonnet_thinking": "low", "interruption_enabled": False,
             **(svc.db.kv_get("knobs", {}) or {}), "muted": st.muted}
    arb = _plain(st.arbiter) if st.arbiter else {}
    return {
        "now": svc.clock.iso(),
        "clock_offset_s": round(svc.clock.offset_s, 3),
        "profile": svc.settings.profile,
        "fake": sorted(svc.settings.fake),
        "incident": _incident(svc),
        "doses": _doses(svc),
        "actions": [dict(r, details=loads(r["details"], {}), screenshots=loads(r["screenshots"], []))
                    for r in svc.db.query("SELECT * FROM actions ORDER BY created_at DESC LIMIT 10")],
        "deep_tasks": [dict(r, result=loads(r["result"], None)) for r in
                       svc.db.query("SELECT id, goal, status, result, started_at, finished_at, cost FROM deep_tasks "
                                    "ORDER BY started_at DESC LIMIT 10")],
        "session": session,
        "arbiter": {"queue": arb.get("queue", []) if isinstance(arb, dict) else []},
        "devices": svc.hub.snapshot() if svc.hub is not None else [],
        "beacons": svc.db.query("SELECT id, last_seen, battery, rssi, last_seq FROM beacons"),
        "spend": _spend(svc),
        "caps": svc.config.caps,
        "knobs": knobs,
        "ui": k.ui.snapshot(),
        "components": {"installed": svc.installed, "reducers": [n for n, _ in k.reducers]},
    }


def basic_health(svc: Services) -> list[dict[str, Any]]:
    """Used when `ahbo.ops.doctor` is not available."""
    rows: list[dict[str, Any]] = []
    try:
        svc.db.one("SELECT 1 AS x")
        rows.append({"item": "database", "status": "green", "detail": str(svc.settings.db_path.name)})
    except Exception as e:
        rows.append({"item": "database", "status": "red", "detail": repr(e)[:120]})
    for item in ("models", "speech", "livekit", "telegram", "twilio", "vision"):
        rows.append({"item": item, "status": "fake" if svc.settings.is_fake(item) else "green",
                     "detail": "fake mode" if svc.settings.is_fake(item) else "configured"})
    n = svc.hub.connected_count if svc.hub else 0
    rows.append({"item": "iPad connected", "status": "green" if n else "red", "detail": f"{n} device(s)"})
    ver = read_manifest(svc).get("version")
    rows.append({"item": "fixed-cache version", "status": "green" if ver else "red", "detail": ver or "missing"})
    rows.append({"item": "clock", "status": "green", "detail": f"{svc.clock.iso()} (offset {svc.clock.offset_s:.0f} s)"})
    du = shutil.disk_usage(svc.settings.data_dir)
    free_gb = du.free / 1e9
    rows.append({"item": "disk", "status": "green" if free_gb > 5 else "red", "detail": f"{free_gb:.1f} GB free"})
    rows.append({"item": "uptime", "status": "green",
                 "detail": svc.extra.get("started_at", datetime.now().isoformat(timespec="seconds"))})
    return rows
