"""Seeded activities (`seed/activities.yaml`, §26.6), shared by the mock centre and the Action Runner.

The seed file is the single source of truth for what the demo centre offers; the mock site's dropdowns and
the runner's 活動/時段 choice both come from here, so they can never drift apart.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ahbo.core.config import load_seed

MOCK_VENUE = "秀茂坪長者中心（示範）"
SESSION_MINUTES = 90
DAY_WORDS = {"mon": "一", "tue": "二", "wed": "三", "thu": "四", "fri": "五", "sat": "六", "sun": "日"}


def load_activities(base_url: str | None = None) -> list[dict[str, Any]]:
    data = load_seed("activities.yaml", base_url)
    return list(data or [])


def find_activity(activity_id: str | None, base_url: str | None = None) -> dict[str, Any] | None:
    if not activity_id:
        return None
    return next((a for a in load_activities(base_url) if a.get("id") == activity_id), None)


def session_value(hhmm: str) -> str:
    """The 時段 option value for a start time: `15:00-16:30`."""
    start = datetime.strptime(hhmm, "%H:%M")
    end = start + timedelta(minutes=SESSION_MINUTES)
    return f"{start:%H:%M}-{end:%H:%M}"


def days_words(days: list[str]) -> str:
    if len(days) >= 7:
        return "每日"
    return "逢星期" + "、".join(DAY_WORDS.get(d, d) for d in days)


def centre_offer(base_url: str | None = None) -> list[dict[str, Any]]:
    """What the mock centre shows: its activities with their sessions (§8.3 form: 活動, 時段)."""
    out = []
    for a in load_activities(base_url):
        if a.get("venue") != MOCK_VENUE:
            continue
        v = session_value(a["time"])
        out.append({
            "id": a["id"],
            "title": a["title"],
            "label": f"{a['title']}（{days_words(a.get('days') or [])}）",
            "sessions": [{"value": v, "label": v.replace("-", "–")}],
        })
    return out
