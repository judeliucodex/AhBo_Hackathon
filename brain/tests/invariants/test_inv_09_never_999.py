"""INV-9: never dial 999 (112, 911) or any non-verified contact. No tool can place a call; the Phone Link refuses
emergency numbers in any formatting and unverified contacts, whoever emits the phone.call command."""
from __future__ import annotations

import pytest

from ahbo.agent.tools import TOOLS, schemas
from ahbo.core.events import Command
from ahbo.core.settings import EMERGENCY_NUMBERS
from inv_helpers import fall, need, until_state


def _twilio_rows(s):
    return [r for r in s.outbox() if r["channel"] in ("twilio", "phone")]


async def test_no_tool_can_place_a_call(system):
    for name in TOOLS:
        assert not any(w in name for w in ("call", "phone", "dial")) or name in ("confirm_action",), name
    for mode in ("companion", "reminder", "reassure", "kernel_listen", "paused"):
        for sch in schemas(mode):
            props = sch["function"]["parameters"].get("properties", {})
            assert not set(props) & {"number", "phone", "to", "to_e164", "e164"}, (sch["function"]["name"], props)


async def test_emergency_numbers_are_listed():
    assert set(EMERGENCY_NUMBERS) >= {"999", "112", "911"}


@pytest.mark.parametrize("phone,verified", [
    ("999", True), ("+852 999", True), ("+852-999", True), ("112", True), ("(911)", True), ("９９９", True),
    ("+85291234567", False),
])
async def test_phone_link_refuses(system, phone, verified):
    s = system
    need(s, "dispatch:phone.call")
    s.db.execute("INSERT INTO contacts(id,name,role,phone,verified,priority) VALUES ('c_x','x','family',?,?,5)",
                 (phone, int(verified)))
    await s.kernel.emit(Command(type="phone.call", priority=0,
                                payload={"contact_id": "c_x", "clip": "P1", "incident_id": "inc_test"}))
    await s.wait(3)
    await s.settle()
    assert not _twilio_rows(s), f"a call was placed to {phone!r} (verified={verified}): {_twilio_rows(s)}"


async def test_phone_link_refuses_raw_numbers(system):
    s = system
    need(s, "dispatch:phone.call")
    await s.kernel.emit(Command(type="phone.call", priority=0,
                                payload={"to": "999", "to_e164": "+852999", "clip": "P1", "incident_id": "inc_t"}))
    await s.wait(3)
    assert not _twilio_rows(s)


async def test_fall_calls_only_verified_contacts(system):
    s = system
    need(s, "reducer:fall", "dispatch:phone.call")
    await fall(s)
    assert await until_state(s, "ESCALATING", 60)
    await s.wait(6)
    rows = _twilio_rows(s)
    assert rows, "the fall escalation placed no call"
    verified = {c["id"] for c in s.db.query("SELECT id FROM contacts WHERE verified=1")}
    for r in rows:
        assert r["to_contact"] in verified, r
        digits = "".join(ch for ch in str(r.get("to_address") or "") if ch.isdigit())
        assert digits not in EMERGENCY_NUMBERS, r
