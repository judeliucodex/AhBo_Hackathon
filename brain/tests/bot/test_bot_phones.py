"""INV-9 from Telegram: numbers are normalised to E.164 before storing; 999/112/911 (any formatting), short codes
and anything the dial guard would refuse are rejected; numbers are shown masked; `verified` needs the confirm step
and, with real Twilio, the number in the account's Outgoing Caller IDs (a GET through httpx.MockTransport here)."""
from __future__ import annotations

from dataclasses import replace

import httpx
import pytest
from bot_helpers import confirm, say, tap

from ahbo.links.bot import phones
from ahbo.links.twilio import guard_call

GOOD = [
    ("91234567", "+85291234567"), ("9123 4567", "+85291234567"), ("+852 9123-4567", "+85291234567"),
    ("(852) 9123 4567", "+85291234567"), ("0085291234567", "+85291234567"), ("８５２ ９１２３ ４５６７", "+85291234567"),
    ("＋852 91234567", "+85291234567"), ("+44 20 7946 0958", "+442079460958"), ("2345 6789", "+85223456789"),
]
EMERGENCY = ["999", "９９９", "+852 999", "(911)", "112", "9-9-9", "00852999", "+1 911", "852 112", "1 2 3 4 5 999"]
OTHER_BAD = [("", "empty"), ("abc", "empty"), ("1823", "short_code"), ("12345", "short_code"),
             ("+852 1234567", "bad_format"), ("+852 123456789", "bad_format"), ("1234567890123456", "bad_format"),
             ("+0 12345678", "bad_format")]


class _Db:
    def __init__(self, row):
        self.row = row

    def one(self, *a, **k):
        return self.row


@pytest.mark.parametrize("raw,e164", GOOD)
def test_numbers_are_normalised_to_e164(raw, e164):
    assert phones.normalise(raw) == (e164, None)
    _c, to, reason = guard_call(_Db({"id": "x", "role": "family", "verified": 1, "phone": e164}),
                                {"contact_id": "x"}, fake=False)
    assert reason is None and to == e164                    # whatever is stored, the dial guard accepts


@pytest.mark.parametrize("raw", EMERGENCY)
def test_emergency_numbers_are_refused_in_any_formatting(raw):
    e164, reason = phones.normalise(raw)
    assert e164 is None and reason in ("emergency_number", "short_code", "bad_format"), (raw, reason)
    if raw in ("999", "９９９", "+852 999", "(911)", "112", "00852999"):
        assert reason == "emergency_number"


@pytest.mark.parametrize("raw,reason", OTHER_BAD)
def test_short_codes_and_bad_formats_are_refused(raw, reason):
    assert phones.normalise(raw) == (None, reason)


@pytest.mark.parametrize("raw", [g for g, _ in GOOD] + EMERGENCY + [b for b, _ in OTHER_BAD] +
                         ["9123 4999", "12345999", "+852 0123 4567", "+852 999 12345", "+1 (212) 555-0199"])
def test_normalise_never_stores_what_the_dial_guard_refuses(raw):
    e164, _ = phones.normalise(raw)
    if e164 is not None:
        _c, to, reason = guard_call(_Db({"id": "x", "role": "family", "verified": 1, "phone": e164}),
                                    {"contact_id": "x"}, fake=False)
        assert reason is None and to == e164, raw


def test_hk_service_prefixes_are_refused_but_numbers_ending_999_are_fine():
    assert phones.normalise("12345999") == (None, "bad_format")       # 1xxx: HK service/short codes
    assert phones.normalise("9123 4999") == ("+85291234999", None)     # a normal mobile that ends in 999


def test_mask_shows_only_the_last_four():
    assert phones.mask("+85291234567") == "••••4567"
    assert phones.mask(None) == "（未有）"


async def test_emergency_number_typed_in_the_form_is_never_stored(svc):
    await tap(svc, "m:f:contact_edit:c_chun")
    await tap(svc, "m:i:_keep")
    for raw in ("999", "+852 999", "112", "(911)", "1823"):
        r = await say(svc, raw)
        assert "⚠️" in r[-1]["text"] and "第 2/4 步" in r[-1]["text"]
    assert svc.db.one("SELECT phone FROM contacts WHERE id='c_chun'")["phone"] == "+85291230000"


def _real_twilio(svc, monkeypatch, caller_ids: list[str]):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET" and request.url.path.endswith("/OutgoingCallerIds.json")
        want = request.url.params.get("PhoneNumber")
        return httpx.Response(200, json={"outgoing_caller_ids": [{"phone_number": n} for n in caller_ids
                                                                 if n == want]})
    monkeypatch.setattr(svc, "settings", replace(svc.settings, fake=svc.settings.fake - {"twilio"}))
    svc.extra["twilio_http_transport"] = httpx.MockTransport(handler)
    return seen


async def _set_phone(svc, number: str):
    await tap(svc, "m:f:contact_edit:c_chun")
    await tap(svc, "m:i:_keep")
    await say(svc, number)
    await tap(svc, "m:i:_keep")
    r = await tap(svc, "m:i:_keep")
    assert "確認即係你確認呢個電話號碼啱" in r[-1]["text"]
    return await confirm(svc, r)


async def test_real_twilio_verifies_only_a_registered_caller_id(svc, monkeypatch):
    seen = _real_twilio(svc, monkeypatch, ["+85298887777"])
    done = await _set_phone(svc, "9888 7777")
    assert "已核實" in done[-1]["text"]
    c = svc.db.one("SELECT phone, verified FROM contacts WHERE id='c_chun'")
    assert c == {"phone": "+85298887777", "verified": 1}
    assert len(seen) == 1 and seen[0].url.params["PhoneNumber"] == "+85298887777"


async def test_real_twilio_unregistered_number_is_stored_unverified(svc, monkeypatch):
    _real_twilio(svc, monkeypatch, [])
    done = await _set_phone(svc, "6555 1234")
    assert "未核實" in done[-1]["text"] and "Verified Caller IDs" in done[-1]["text"]
    assert svc.db.one("SELECT phone, verified FROM contacts WHERE id='c_chun'") == {"phone": "+85265551234",
                                                                                   "verified": 0}
    # later, after she registers it in Twilio: 核實電話 re-checks
    _real_twilio(svc, monkeypatch, ["+85265551234"])
    r = await tap(svc, "m:a:verify:c_chun")
    assert "核實 阿珍 嘅電話 ••••1234" in r[-1]["text"]
    done = await confirm(svc, r)
    assert svc.db.one("SELECT verified FROM contacts WHERE id='c_chun'")["verified"] == 1


async def test_twilio_lookup_failure_leaves_it_unverified(svc, monkeypatch):
    monkeypatch.setattr(svc, "settings", replace(svc.settings, fake=svc.settings.fake - {"twilio"}))
    svc.extra["twilio_http_transport"] = httpx.MockTransport(lambda req: httpx.Response(500, json={}))
    done = await _set_phone(svc, "6555 9999")
    assert "未核實" in done[-1]["text"]
    assert svc.db.one("SELECT verified FROM contacts WHERE id='c_chun'")["verified"] == 0


async def test_unchanged_number_keeps_its_verification_without_a_lookup(svc, monkeypatch):
    seen = _real_twilio(svc, monkeypatch, [])
    await tap(svc, "m:f:contact_edit:c_chun")
    await say(svc, "阿珍姐")
    for _ in range(3):
        r = await tap(svc, "m:i:_keep")
    await confirm(svc, r)
    assert svc.db.one("SELECT name, verified FROM contacts WHERE id='c_chun'") == {"name": "阿珍姐", "verified": 1}
    assert seen == []
