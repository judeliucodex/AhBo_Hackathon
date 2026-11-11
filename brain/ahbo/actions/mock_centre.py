"""The mock elderly-centre registration API (§8.3, §26.4): `POST /mock-centre/api/register`. Public, no
auth. The static page itself (`GET /mock-centre/…`) is served by the platform (`api/routes/public.py`,
BUILD_LOG D-3) — this router provides only the API routes. Clearly labelled 示範用 Demo. A fictional centre:
nothing here is a real organisation, and the only data it ever receives is the fictional seed profile.

Additions (documented in the final report / BUILD_LOG request):
- table `mock_registrations`, created at install time with CREATE TABLE IF NOT EXISTS (not in core/db.py,
  because the demo site is not part of the care data model and is never reset with it);
- `GET /mock-centre/api/activities`: the centre's activities from `seed/activities.yaml`, so the form's
  dropdowns have a single source of truth.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ahbo.core.clock import get_clock
from ahbo.core.db import Database
from ahbo.core.settings import REPO_ROOT

from .activities import centre_offer

log = logging.getLogger("ahbo.actions.mock_centre")

STATIC_DIR = REPO_ROOT / "mock-centre"

MOCK_REGISTRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS mock_registrations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, reference TEXT UNIQUE, ts TEXT NOT NULL,
  name_zh TEXT NOT NULL, name_en TEXT, age_group TEXT NOT NULL, phone TEXT NOT NULL,
  activity_id TEXT NOT NULL, session TEXT NOT NULL
);
"""

AGE_GROUPS = ("60-64", "65-69", "70-74", "75-79", "80-84", "85+")
BLOCKED_VARIANTS = ("hkid", "captcha", "login", "payment")
REF_OFFSET = 422            # the first booking is DEMO-0423

router = APIRouter()
_db: Database | None = None


def bind(db: Database) -> None:
    """Give the router its database and create the demo table (idempotent)."""
    global _db
    db.execute(MOCK_REGISTRATIONS_SQL)
    _db = db


def reference_for(rowid: int) -> str:
    return f"DEMO-{(rowid + REF_OFFSET) % 10000:04d}"


class Registration(BaseModel):
    name_zh: str = Field("", max_length=40)
    name_en: str = Field("", max_length=60)
    age_group: str = ""
    phone: str = Field("", max_length=32)
    activity: str = ""
    session: str = ""
    variant: str | None = None


def _phone_ok(phone: str) -> bool:
    # 8+ digits (HK numbers, optionally +852), or the seed's demo placeholder 「（示範號碼）」 when no
    # FAMILY_PHONE is configured — this is a demo site and never contacts anyone.
    return len(re.sub(r"\D", "", phone)) >= 8 or "示範" in phone


def validate(reg: Registration) -> tuple[dict[str, Any] | None, str | None]:
    """Return (normalised record, None) or (None, Cantonese error message)."""
    if reg.variant and reg.variant.lower() in BLOCKED_VARIANTS:
        return None, "此版本需要額外資料，請親身到中心辦理。"
    if not reg.name_zh.strip():
        return None, "請填寫中文姓名。"
    if reg.age_group not in AGE_GROUPS:
        return None, "請選擇年齡組別。"
    if not _phone_ok(reg.phone):
        return None, "請填寫有效的聯絡電話（8位數字）。"
    offer = {a["id"]: a for a in centre_offer()}
    act = offer.get(reg.activity)
    if act is None:
        return None, "請選擇活動。"
    sess = next((s for s in act["sessions"] if s["value"] == reg.session), None)
    if sess is None:
        return None, "請選擇時段。"
    return {"name_zh": reg.name_zh.strip(), "name_en": reg.name_en.strip(), "age_group": reg.age_group,
            "phone": reg.phone.strip(), "activity_id": act["id"], "session": sess["value"],
            "activity_label": act["title"], "session_label": sess["label"]}, None


@router.get("/mock-centre/api/activities")
async def mock_centre_activities() -> dict[str, Any]:
    return {"ok": True, "activities": centre_offer()}


@router.post("/mock-centre/api/register")
async def mock_centre_register(reg: Registration) -> JSONResponse:
    if _db is None:
        raise HTTPException(503, "mock centre not installed")
    rec, err = validate(reg)
    if err:
        return JSONResponse({"ok": False, "message": err}, status_code=400)
    assert rec is not None
    with _db.transaction():
        cur = _db.execute(
            "INSERT INTO mock_registrations(ts,name_zh,name_en,age_group,phone,activity_id,session) "
            "VALUES (?,?,?,?,?,?,?)",
            (get_clock().iso(), rec["name_zh"], rec["name_en"], rec["age_group"], rec["phone"],
             rec["activity_id"], rec["session"]))
        ref = reference_for(int(cur.lastrowid or 0))
        _db.execute("UPDATE mock_registrations SET reference=? WHERE id=?", (ref, cur.lastrowid))
    log.info("mock centre registration %s for %s", ref, rec["activity_id"])
    return JSONResponse({"ok": True, "reference": ref, "activity_label": rec["activity_label"],
                         "session_label": rec["session_label"]})


def registrations(db: Database) -> list[dict[str, Any]]:
    return db.query("SELECT * FROM mock_registrations ORDER BY id")


def static_dir() -> Path:
    return STATIC_DIR
