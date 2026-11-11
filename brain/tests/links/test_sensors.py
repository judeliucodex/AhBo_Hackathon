"""Sensor ingest (§9.1): token auth, idempotency on (beacon_id, seq), the queued 202, and the reference
detection rule as a pure function."""
from __future__ import annotations

import asyncio
import time

from ahbo.links.sensors import detect_fall

from link_helpers import BEACON_TOKEN, events_of, svc_of

FALL_BODY = {"beacon_id": "beacon1", "type": "fall_suspected", "ts": "2026-11-20T15:00:01+08:00", "seq": 1,
             "peak_g": 3.1, "freefall_ms": 180, "stillness_s": 2.4, "battery": 0.81, "rssi": -58}


def test_unknown_or_wrong_token_is_rejected(client):
    r = client.post("/events/sensor", json=FALL_BODY)                       # no header
    assert r.status_code == 401
    r = client.post("/events/sensor", json=FALL_BODY, headers={"X-Beacon-Token": "wrong"})
    assert r.status_code == 401
    r = client.post("/events/sensor", json={**FALL_BODY, "beacon_id": "ghost"},   # unknown beacon
                    headers={"X-Beacon-Token": BEACON_TOKEN})
    assert r.status_code == 401


def test_bad_type_and_bad_body_are_rejected(client):
    h = {"X-Beacon-Token": BEACON_TOKEN}
    assert client.post("/events/sensor", json={**FALL_BODY, "type": "panic"}, headers=h).status_code == 400
    assert client.post("/events/sensor", json={"type": "sos"}, headers=h).status_code == 400
    assert client.post("/events/sensor", data="not json", headers=h).status_code == 400


def test_event_is_queued_fast_and_idempotent(client):
    svc = svc_of(client)
    h = {"X-Beacon-Token": BEACON_TOKEN}
    t0 = time.monotonic()
    r = client.post("/events/sensor", json=FALL_BODY, headers=h)
    assert time.monotonic() - t0 < 2.0
    assert r.status_code == 202 and r.json() == {"ok": True, "queued": True}
    r2 = client.post("/events/sensor", json=FALL_BODY, headers=h)            # same (beacon_id, seq)
    assert r2.status_code == 202 and r2.json() == {"ok": True, "duplicate": True}
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not events_of(svc, "sensor.fall_suspected"):
        time.sleep(0.02)
    evs = events_of(svc, "sensor.fall_suspected")
    assert len(evs) == 1                                                     # the duplicate is never re-emitted
    assert evs[0]["source"] == "beacon" and evs[0]["payload"]["peak_g"] == 3.1


def test_sos_and_heartbeat_update_the_beacon_row(client):
    svc = svc_of(client)
    h = {"X-Beacon-Token": BEACON_TOKEN}
    assert client.post("/events/sensor", json={**FALL_BODY, "type": "sos", "seq": 2}, headers=h).status_code == 202
    assert client.post("/events/sensor", json={**FALL_BODY, "type": "heartbeat", "seq": 3, "battery": 0.66},
                       headers=h).status_code == 202
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and len(events_of(svc, "sensor.heartbeat")) < 1:
        time.sleep(0.02)
    assert len(events_of(svc, "sensor.sos_pressed")) == 1
    b = svc.db.one("SELECT * FROM beacons WHERE id='beacon1'")
    assert b["battery"] == 0.66 and b["last_seq"] == 3 and b["last_seen"]


def test_missing_seq_is_processed_but_not_recorded_as_seen(client):
    svc = svc_of(client)
    body = {k: v for k, v in FALL_BODY.items() if k != "seq"}
    assert client.post("/events/sensor", json=body, headers={"X-Beacon-Token": BEACON_TOKEN}).status_code == 202
    assert svc.db.one("SELECT COUNT(*) n FROM sensor_seen")["n"] == 0


# ---------------------------------------------------------------- the reference detection rule (§9.1)
def _samples() -> list[dict[str, float]]:
    out = []
    for i in range(10):                       # 0.10–0.19 s: free fall at 0.1 g
        t = 0.10 + i * 0.01
        out.append({"t": t, "g": 0.1})
    out.append({"t": 0.25, "g": 3.1})          # impact
    for i in range(30):                        # 0.30–2.59 s: still at ~1 g
        out.append({"t": 0.30 + i * 0.08, "g": 1.0 + (0.02 if i % 3 else -0.02)})
    return out


def test_detect_fall_matches_the_reference_rule():
    res = detect_fall(_samples())
    assert res is not None
    assert res["peak_g"] == 3.1
    assert res["freefall_ms"] == 90.0
    assert res["stillness_s"] >= 2.0


def test_detect_fall_rejects_incomplete_patterns():
    short_freefall = [{"t": 0.10, "g": 0.2}, {"t": 0.11, "g": 0.2}, {"t": 0.12, "g": 0.2},
                      {"t": 0.125, "g": 3.2}] + \
        [{"t": 0.3 + i * 0.1, "g": 1.0} for i in range(30)]
    assert detect_fall(short_freefall) is None                     # free-fall < 60 ms
    no_impact = [{"t": 0.10 + i * 0.01, "g": 0.2} for i in range(8)] + [{"t": 0.25, "g": 1.8}] + \
        [{"t": 0.3 + i * 0.1, "g": 1.0} for i in range(30)]
    assert detect_fall(no_impact) is None                          # impact ≤ 2.5 g
    short_still = [{"t": 0.10 + i * 0.01, "g": 0.2} for i in range(8)] + [{"t": 0.25, "g": 3.2}] + \
        [{"t": 0.3 + i * 0.1, "g": 1.0} for i in range(15)]
    assert detect_fall(short_still) is None                        # stillness < 2 s
    assert detect_fall([]) is None
    assert detect_fall([(0.0, 1.0), (0.05, 0.3), (1.0, 1.0)]) is None


def test_detect_fall_accepts_tuple_samples():
    res = detect_fall([(t, g) for t, g in ((s["t"], s["g"]) for s in _samples())])
    assert res is not None and res["peak_g"] == 3.1
