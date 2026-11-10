"""INV-9, Kernel side: the fall flow only ever asks to call verified contacts with an allowed role, and never an
emergency number in any formatting (the Phone Link enforces the same again)."""
from __future__ import annotations

import pytest
from kt import inc, of, timer, until

from ahbo.kernel.common import call_order, callable_contact, is_emergency_number


@pytest.mark.parametrize("phone", ["999", "+852 999", "+852-999", "112", "(911)", "９９９", "00852 999", "1-1-2",
                                   "+852 9 9 9", "911", "+1 911", "+44 112"])
def test_emergency_numbers_rejected(phone):
    assert is_emergency_number(phone)


@pytest.mark.parametrize("phone", ["+85291234567", "+852 6123 4567", "+85291234999"])
def test_normal_numbers_allowed(phone):
    assert not is_emergency_number(phone)


def test_contact_rules():
    base = {"verified": 1, "role": "family", "phone": "+85291234567"}
    assert callable_contact(base)
    assert not callable_contact({**base, "verified": 0})
    assert not callable_contact({**base, "role": "centre"})
    assert not callable_contact({**base, "phone": "999"})


async def test_fall_never_calls_bad_contacts(kh):
    h = kh
    db = h.kernel.db
    db.execute("INSERT INTO contacts(id,name,role,phone,verified,priority) VALUES "
               "('c_999','x','emergency_centre_sim','999',1,0), ('c_unv','y','family','+85291234567',0,0), "
               "('c_ctr','z','centre','+85291234568',1,0), ('c_fw','w','family','＋８５２ ９９９',1,0)")
    allowed = {c["id"] for c in call_order(h.kernel.ctx())}
    assert allowed == {"c_ecs", "c_chun"}
    await h.kernel.process(__import__("ahbo.core.events", fromlist=["Event"]).Event(
        type="sensor.fall_suspected", source="beacon", payload={}))
    assert await until(lambda: inc(h)["state"] in ("ESCALATING", "REASSURING"), 8)
    iid = inc(h)["id"]
    for _ in range(4):
        await timer(h, f"fall:tack:{iid}", incident_id=iid)
    called = [c["payload"]["contact_id"] for c in of(h, "phone.call")]
    assert called == ["c_ecs", "c_chun", "c_ecs"] and set(called) <= {"c_ecs", "c_chun"}
