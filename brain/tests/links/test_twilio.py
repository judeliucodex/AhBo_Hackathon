"""Twilio callbacks: RequestValidator signatures (good → 200, bad/missing → 403), the TwiML dry run behind the
operator cookie, the status/gather mapping to `phone.call_status`, and the real adapter against a mocked SDK."""
from __future__ import annotations

from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from ahbo.links.twilio import call_twiml

from link_helpers import BASE_URL, TFL_TOKEN, events_of, login, make_settings, svc_of


def _signed(path: str, params: dict[str, str]) -> dict[str, str]:
    """Headers for a request Twilio would have signed (the test computes the signature with the same SDK)."""
    sig = RequestValidator(TFL_TOKEN).compute_signature(BASE_URL + path, params)
    return {"X-Twilio-Signature": sig}


def test_twiml_route_signature(client):
    path = "/twilio/twiml/call_t1"
    params = {"CallSid": "CA123", "To": "+85298765432"}
    r = client.post(path, data=params)                                   # no signature
    assert r.status_code == 403
    r = client.post(path, data=params, headers={"X-Twilio-Signature": "bogus"})
    assert r.status_code == 403
    r = client.post(path, data=params, headers=_signed(path, params))
    assert r.status_code == 200
    body = r.text
    assert '<Play loop="2">' in body and "<Gather" in body and 'numDigits="1"' in body
    assert "/twilio/gather?call_id=call_t1" in body


def test_twiml_route_carries_the_public_clip_url(client):
    path = "/twilio/twiml/call_t2"
    r = client.post(path, data={}, headers=_signed(path, {}))
    svc = svc_of(client)
    clip_path = svc.db.kv_get("public_clip_path")
    assert clip_path and clip_path.startswith("/media/public/") and clip_path.endswith("/P1.m4a")
    assert clip_path in r.text


def test_status_and_gather_routes(client):
    svc = svc_of(client)
    from ahbo.links import outbox
    oid = outbox.record(svc.db, ts=svc.clock.iso(), channel="twilio", kind="CALL", to_contact="c_ecs",
                        to_address="", payload={"call_id": "call_t9"}, fake=True, correlation_id="inc_t9",
                        provider_id="call_t9")
    path = "/twilio/status?call_id=call_t9"
    params = {"CallStatus": "ringing", "CallSid": "CA9"}
    r = client.post(path, data=params, headers=_signed(path, params))
    assert r.status_code == 200
    evs = events_of(svc, "phone.call_status")
    assert evs[-1]["payload"]["status"] == "ringing"
    assert evs[-1]["payload"]["outbox_id"] == oid
    assert evs[-1]["payload"]["incident_id"] == "inc_t9"
    path = "/twilio/gather?call_id=call_t9"
    params = {"Digits": "1", "CallSid": "CA9"}
    r = client.post(path, data=params, headers=_signed(path, params))
    assert r.status_code == 200 and "<Hangup/>" in r.text
    evs = events_of(svc, "phone.call_status")
    assert evs[-1]["payload"]["status"] == "acknowledged"
    assert svc.db.one("SELECT status FROM outbox WHERE id=?", (oid,))["status"] == "acknowledged"
    path = "/twilio/status?call_id=call_t9"
    params = {"CallStatus": "completed", "CallSid": "CA9"}
    client.post(path, data=params, headers=_signed(path, params))
    assert events_of(svc, "phone.call_status")[-1]["payload"]["status"] == "completed"


def test_gather_wrong_digit_replays(client):
    path = "/twilio/gather?call_id=call_rx"
    params = {"Digits": "5", "CallSid": "CAx"}
    r = client.post(path, data=params, headers=_signed(path, params))
    assert r.status_code == 200 and "<Redirect" in r.text and "/twilio/twiml/call_rx" in r.text


def test_unknown_status_is_ignored(client):
    path = "/twilio/status?call_id=none"
    params = {"CallStatus": "weird", "CallSid": "CAx"}
    r = client.post(path, data=params, headers=_signed(path, params))
    assert r.status_code == 200


def test_twiml_test_dry_run_needs_the_operator_cookie(client):
    r = client.get("/twilio/twiml/test")
    assert r.status_code == 401
    login(client)
    r = client.get("/twilio/twiml/test")
    assert r.status_code == 200
    assert '<Play loop="2">' in r.text and "<Gather" in r.text
    assert not svc_of(client).db.one("SELECT id FROM outbox WHERE channel='twilio'")   # no call placed


def test_twiml_xml_escapes_urls():
    xml = call_twiml("https://x.invalid/a?b=1&c=2", "https://x.invalid/g?a=1&b=2")
    assert "&amp;" in xml
    assert 'action="https://x.invalid/g?a=1&amp;b=2"' in xml


# ---------------------------------------------------------------- the real adapter with a mocked SDK
def _svc_for_real_adapter(tmp_path):
    import asyncio

    from ahbo.core.clock import FakeClock, set_clock
    from ahbo.core.db import open_db
    from ahbo.core.config import load_config

    clock = FakeClock("2026-11-20T15:00:00+08:00")
    set_clock(clock)
    settings = make_settings(tmp_path)
    db = open_db(":memory:")
    db.execute("INSERT INTO contacts(id,name,role,phone,verified,priority) VALUES "
               "('c_ecs','緊急中心（模擬）','emergency_centre_sim','+85298765432',1,0)")
    config = load_config(fresh=True)
    statuses: list[object] = []

    class _Kernel:
        def note(self, ev) -> None:
            return None

        async def submit(self, ev) -> None:
            statuses.append(ev)

        def spawn(self, coro, name="bg"):
            return asyncio.ensure_future(coro)

    _Kernel.clock = clock          # after the class body: a `clock = clock` inside it would raise NameError

    svc = type("SVC", (), {"kernel": _Kernel(), "db": db, "clock": clock, "settings": settings,
                           "config": config, "extra": {}})()
    return svc, db, statuses


def test_twilio_real_places_the_call_via_the_sdk(monkeypatch, tmp_path):
    import asyncio

    import twilio.rest
    from ahbo.links import outbox
    from ahbo.links.twilio import TwilioReal

    class FakeCall:
        sid = "CASID123"

    class FakeCallList:
        kw: dict = {}

        def create(self, **kw):
            FakeCallList.kw = kw
            return FakeCall()

    class FakeClient:
        def __init__(self, sid, token):
            self.calls = FakeCallList()

    monkeypatch.setattr(twilio.rest, "Client", FakeClient)
    svc, db, _ = _svc_for_real_adapter(tmp_path)
    oid = outbox.record(db, ts=svc.clock.iso(), channel="twilio", kind="CALL", to_contact="c_ecs",
                        to_address="+85298765432", payload={"call_id": "call_r1"}, fake=False, status="queued",
                        provider_id="call_r1")
    res = asyncio.run(TwilioReal(svc).call(to_e164="+85298765432", contact_id="c_ecs", call_id="call_r1",
                                           clip_url=f"{BASE_URL}/media/public/x/P1.m4a",
                                           correlation_id="inc_r", outbox_id=oid))
    assert res["ok"] and res["provider_id"] == "CASID123"
    kw = FakeCallList.kw
    assert kw["to"] == "+85298765432" and kw["from_"] == svc.settings.twilio_from
    assert kw["url"] == f"{BASE_URL}/twilio/twiml/call_r1"
    assert kw["status_callback"] == f"{BASE_URL}/twilio/status?call_id=call_r1"
    row = outbox.get(db, oid)
    assert row["payload"]["call_sid"] == "CASID123"
    assert row["provider_id"] == "call_r1"        # our call_id stays the TwiML lookup key
    db.close()


def test_twilio_real_failure_emits_failed_status(monkeypatch, tmp_path):
    import asyncio

    import twilio.rest
    from ahbo.links import outbox
    from ahbo.links.twilio import TwilioReal

    class FakeClient:
        def __init__(self, sid, token):
            self.calls = self

        def create(self, **kw):
            raise RuntimeError("twilio down (drill)")

    monkeypatch.setattr(twilio.rest, "Client", FakeClient)
    svc, db, statuses = _svc_for_real_adapter(tmp_path)
    oid = outbox.record(db, ts=svc.clock.iso(), channel="twilio", kind="CALL", to_contact="c_ecs",
                        to_address="+85298765432", payload={"call_id": "call_r2"}, fake=False, status="queued",
                        provider_id="call_r2")
    res = asyncio.run(TwilioReal(svc).call(to_e164="+85298765432", contact_id="c_ecs", call_id="call_r2",
                                           clip_url=f"{BASE_URL}/x", correlation_id="inc_r2", outbox_id=oid))
    assert res["ok"] is False
    assert outbox.get(db, oid)["status"] == "failed"
    assert any(ev.payload.get("status") == "failed" for ev in statuses)
    db.close()
