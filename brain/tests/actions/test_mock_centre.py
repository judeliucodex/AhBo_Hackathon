"""The mock centre registration API (`actions/mock_centre.py`, §8.3/§26.4): validation, booking reference,
the hard-stop variants, and the `mock_registrations` table created at install time."""
from __future__ import annotations

import json

from ahbo.actions import mock_centre
from ahbo.core.clock import FakeClock, set_clock
from ahbo.core.db import open_db


def setup_db():
    set_clock(FakeClock("2026-11-20T10:00:00+08:00"))
    db = open_db(":memory:")
    mock_centre.bind(db)
    return db


async def test_register_returns_booking_reference():
    db = setup_db()
    body = {"name_zh": "王婆婆", "name_en": "Wong Po-po", "age_group": "75-79", "phone": "（示範號碼）",
            "activity": "act_opera_1500", "session": "15:00-16:30"}
    r = await mock_centre.mock_centre_register(mock_centre.Registration(**body))
    assert r.status_code == 200
    data = json.loads(r.body)
    assert data["ok"] is True and data["reference"] == "DEMO-0423"
    assert data["activity_label"] == "粵曲班"
    r2 = await mock_centre.mock_centre_register(mock_centre.Registration(**{**body, "name_en": ""}))
    assert json.loads(r2.body)["reference"] == "DEMO-0424"
    rows = mock_centre.registrations(db)
    assert [x["reference"] for x in rows] == ["DEMO-0423", "DEMO-0424"]
    assert rows[0]["ts"].endswith("+08:00")


async def test_register_rejects_incomplete_forms_in_cantonese():
    good = {"name_zh": "王婆婆", "age_group": "75-79", "phone": "91234567",
            "activity": "act_opera_1500", "session": "15:00-16:30"}
    bad = [
        {},                                                    # everything missing
        {**good, "name_zh": " "},                              # no name
        {**good, "age_group": "12-14"},                        # not a real group
        {**good, "phone": "123"},                              # not 8 digits, not the demo placeholder
        {**good, "activity": "act_nope"},                      # unknown activity
        {**good, "session": "99:99-00:00"},                    # unknown session
    ]
    for body in bad:
        r = await mock_centre.mock_centre_register(mock_centre.Registration(**body))
        assert r.status_code == 400, body
        data = json.loads(r.body)
        assert data["ok"] is False and data["message"], body


async def test_hard_stop_variants_are_refused_server_side():
    good = {"name_zh": "王婆婆", "age_group": "75-79", "phone": "91234567",
            "activity": "act_opera_1500", "session": "15:00-16:30"}
    for variant in mock_centre.BLOCKED_VARIANTS:
        r = await mock_centre.mock_centre_register(mock_centre.Registration(**good, variant=variant))
        assert r.status_code == 400
        assert json.loads(r.body)["ok"] is False


async def test_activities_endpoint_serves_the_seed():
    r = await mock_centre.mock_centre_activities()
    assert r["ok"] is True
    ids = {a["id"] for a in r["activities"]}
    assert {"act_opera_1500", "act_craft_1430"} <= ids           # both at the mock centre's venue
    opera = next(a for a in r["activities"] if a["id"] == "act_opera_1500")
    assert opera["sessions"][0]["value"] == "15:00-16:30"


async def test_bind_creates_table_idempotently():
    db = setup_db()
    mock_centre.bind(db)                                          # second install must not fail
    assert db.one("SELECT name FROM sqlite_master WHERE name='mock_registrations'")
