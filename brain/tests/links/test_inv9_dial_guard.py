"""INV-9 at the links layer: the independent dial guard. Emergency numbers (999/112/911) in any formatting,
short codes, partial numbers, raw payload numbers, unverified contacts, wrong roles and unknown contacts are
all refused before any outbox row is written; a verified, callable number is allowed."""
from __future__ import annotations

import pytest

from ahbo.core.db import open_db
from ahbo.core.events import Command
from ahbo.links.twilio import guard_call

GOOD = "+85291234567"


@pytest.fixture
def db():
    db = open_db(":memory:")
    db.execute("INSERT INTO contacts(id,name,role,phone,verified,priority) VALUES "
               "('c_ok','阿珍','family',?,1,1)", (GOOD,))
    return db


@pytest.mark.parametrize("phone,verified,role,fake,reason", [
    ("999", 1, "family", True, "emergency_number"),
    ("+852 999", 1, "family", True, "emergency_number"),
    ("112", 1, "family", True, "emergency_number"),
    ("(911)", 1, "family", True, "emergency_number"),
    (" 9 9 9 ", 1, "family", True, "emergency_number"),
    ("+852-999", 1, "family", True, "emergency_number"),
    ("00852-999", 1, "family", True, "emergency_number"),
    ("＋８５２９９９", 1, "family", True, "emergency_number"),
    ("+1 911", 1, "family", True, "emergency_number"),
    ("9991234", 1, "family", True, "short_or_missing_number"),   # partial 7-digit number is never dialled
    ("123999", 1, "family", False, "emergency_number"),          # ends in 999 with ≤ 5 digits before it
    ("1234", 1, "family", True, "short_or_missing_number"),      # ≤ 4 digits
    ("9123456", 1, "family", False, "short_or_missing_number"),  # partial number (< 8 digits)
    ("", 1, "family", False, "short_or_missing_number"),         # nothing to dial in real mode
    (GOOD, 0, "family", True, "unverified"),
    (GOOD, 0, "family", False, "unverified"),
    (GOOD, 1, "centre", True, "role_not_allowed"),
    (GOOD, 1, "neighbour", False, "role_not_allowed"),
])
def test_refused_formats(db, phone, verified, role, fake, reason):
    db.execute("UPDATE contacts SET phone=?, verified=?, role=? WHERE id='c_ok'", (phone, verified, role))
    contact, e164, got = guard_call(db, {"contact_id": "c_ok"}, fake=fake)
    assert (contact, got) == (None, reason), (phone, verified, role, got)


@pytest.mark.parametrize("payload", [
    {"to": "999"}, {"to_e164": "+852999"}, {"number": "91234567"}, {"phone": "+85291234567"},
    {"contact_id": "c_ghost"},
])
def test_refused_payloads(db, payload):
    contact, _, got = guard_call(db, payload, fake=True)
    assert contact is None
    assert got in ("no_raw_numbers", "unknown_contact")


def test_good_verified_number_is_allowed(db):
    contact, e164, reason = guard_call(db, {"contact_id": "c_ok"}, fake=False)
    assert reason is None
    assert contact["id"] == "c_ok"
    assert e164 == GOOD


def test_emergency_centre_sim_is_callable(db):
    db.execute("INSERT INTO contacts(id,name,role,phone,verified,priority) VALUES "
               "('c_ecs','緊急中心（模擬）','emergency_centre_sim','+85298765432',1,0)")
    contact, e164, reason = guard_call(db, {"contact_id": "c_ecs"}, fake=False)
    assert reason is None and e164 == "+85298765432"


def test_fake_call_with_no_number_is_allowed_only_in_fake_mode(db):
    db.execute("UPDATE contacts SET phone=NULL WHERE id='c_ok'")
    contact, e164, reason = guard_call(db, {"contact_id": "c_ok"}, fake=True)
    assert reason is None and e164 == ""
    assert guard_call(db, {"contact_id": "c_ok"}, fake=False)[2] == "short_or_missing_number"


# ---------------------------------------------------------------- dispatcher integration (no outbox row on refusal)
async def test_dispatcher_refusal_leaves_no_outbox_row(system):
    s = system
    s.db.execute("INSERT INTO contacts(id,name,role,phone,verified,priority) VALUES "
                 "('c_x','x','family','999',1,5)")
    await s.kernel.emit(Command(type="phone.call", priority=0,
                                payload={"contact_id": "c_x", "clip": "P1", "incident_id": "inc_t"}))
    await s.kernel.emit(Command(type="phone.call", priority=0,
                                payload={"to": "999", "to_e164": "+852999", "clip": "P1", "incident_id": "inc_t"}))
    await s.wait(2)
    await s.settle()
    assert not [r for r in s.outbox() if r["channel"] == "twilio"]
    refusals = s.events("phone.call_refused")
    assert {r["payload"]["reason"] for r in refusals} >= {"emergency_number", "no_raw_numbers"}


async def test_dispatcher_places_a_fake_call_for_a_verified_contact(system):
    s = system
    await s.kernel.emit(Command(type="phone.call", priority=0,
                                payload={"contact_id": "c_ecs", "clip": "P1", "incident_id": "inc_ok"}))
    assert await s.wait_for(lambda: [r for r in s.outbox() if r["channel"] == "twilio"], 5)
    rows = [r for r in s.outbox() if r["channel"] == "twilio"]
    assert len(rows) == 1
    r = rows[0]
    assert r["to_contact"] == "c_ecs" and r["fake"] and r["payload"]["call_id"]
    assert await s.wait_for(lambda: s.events("phone.call_status"), 5)
    statuses = [(e["payload"]["status"], e["payload"]["call_id"]) for e in s.events("phone.call_status")]
    assert ("ringing", r["payload"]["call_id"]) in statuses


async def test_daily_cap_refuses_non_safety_calls_but_not_safety_calls(system):
    s = system
    from ahbo.core.ids import new_id
    day = s.clock.today().isoformat()
    for _ in range(5):                       # cap twilio_calls: 5 (config/caps.yaml)
        s.db.execute("INSERT INTO costs(id, ts, provider, purpose, usd) VALUES (?,?,?,?,0)",
                     (new_id("cost"), f"{day}T12:00:00+08:00", "twilio", "phone_call"))
    await s.kernel.emit(Command(type="phone.call", priority=0,
                                payload={"contact_id": "c_chun", "clip": "P1"}))   # no incident: daily cap applies
    await s.wait(1)
    assert not [r for r in s.outbox() if r["channel"] == "twilio"]
    assert any(r["payload"]["reason"] == "cap_reached" for r in s.events("phone.call_refused"))
    await s.kernel.emit(Command(type="phone.call", priority=0,
                                payload={"contact_id": "c_ecs", "clip": "P1", "incident_id": "inc_safety"}))
    await s.wait(1)
    rows = [r for r in s.outbox() if r["channel"] == "twilio"]
    assert len(rows) == 1 and rows[0]["correlation_id"] == "inc_safety"   # safety calls exempt (≤ 3/incident)
