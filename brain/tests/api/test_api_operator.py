"""Operator API (CONTRACTS §9): snapshot, triggers, overrides (INV-8), clock, reset, panic, scenarios, SSE."""
from __future__ import annotations

import json
import time

from platform_app import APP_SECRET, hello, login, svc_of

AUTH = {"Authorization": f"Bearer {APP_SECRET}"}


def _events(svc, prefix: str) -> list[dict]:
    return svc.db.query("SELECT type, source, payload FROM events WHERE type LIKE ? ORDER BY rowid", (prefix + "%",))


def _wait(pred, timeout=5.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_state_snapshot_shape(client):
    login(client)
    s = client.get("/operator/api/state").json()
    for key in ("now", "clock_offset_s", "profile", "fake", "incident", "doses", "actions", "deep_tasks", "session",
                "arbiter", "devices", "beacons", "spend", "caps", "knobs", "ui"):
        assert key in s, key
    assert s["now"].startswith("2026-11-20T10:00")
    assert [d["slot_id"] for d in s["doses"]] == ["s_0800", "s_1200", "s_2000"]
    assert s["doses"][0]["state"] == "taken_self_reported"
    assert s["ui"]["mood"] == "idle" and s["incident"] is None


def test_clock_endpoints(client):
    login(client)
    r = client.post("/operator/api/clock", json={"set": "07:58"}).json()
    assert r["ok"] and r["now"][11:16] == "07:58"
    r = client.post("/operator/api/clock", json={"anchor": "S2"}).json()
    assert r["now"][11:16] == "10:00"
    r = client.post("/operator/api/clock", json={"offset_s": 3600}).json()
    assert r["offset_s"] == 3600
    svc = svc_of(client)
    assert svc.db.kv_get("clock_offset_s") == 3600
    assert any(e["type"] == "operator.clock" and e["source"] == "operator" for e in _events(svc, "operator."))
    assert client.post("/operator/api/clock", json={"anchor": "S9"}).status_code == 400


def test_utterance_trigger_and_turns(client):
    login(client)
    svc = svc_of(client)
    r = client.post("/operator/api/trigger", json={"kind": "utterance", "text": "阿寶，我幾時食藥呀？"})
    assert r.status_code == 200 and r.json()["ok"]
    assert _wait(lambda: _events(svc, "agent.reply_start"))
    reply = json.loads(_events(svc, "agent.reply_start")[0]["payload"])["text"]
    assert "十二點" in reply


def test_overrides_are_operator_events(client):
    login(client)
    svc = svc_of(client)
    for body in ({"kind": "mute"}, {"kind": "unmute"}, {"kind": "incident_ok"},
                 {"kind": "force_tool_result", "tool": "get_med_status", "result": {"ok": True}},
                 {"kind": "provider", "item": "stt", "to": "fallback"},
                 {"kind": "knobs", "flash_thinking": "low"}, {"kind": "play_fixed", "clip_id": "F5"},
                 {"kind": "say", "text": "測試一下"}):
        r = client.post("/operator/api/override", json=body)
        assert r.status_code == 200, (body, r.text)
    types = [e["type"] for e in _events(svc, "operator.")]
    for t in ("operator.mute", "operator.unmute", "operator.ok", "operator.force_tool_result",
              "operator.provider_switch", "operator.model_config", "operator.play_fixed", "operator.say"):
        assert t in types, t
    assert client.post("/operator/api/override", json={"kind": "play_fixed", "clip_id": "NOPE"}).status_code == 400
    assert client.get("/operator/api/state").json()["knobs"]["flash_thinking"] == "low"


def test_reset_demo_clears_and_resets_face(client):
    login(client)
    svc = svc_of(client)
    with client.websocket_connect("/ws/device", headers=AUTH) as ws:
        hello(ws)
        ws.receive_json()
        client.post("/operator/api/trigger", json={"kind": "wake"})
        assert _wait(lambda: svc.kernel.state.session is not None)
        t0 = time.monotonic()
        r = client.post("/operator/api/trigger", json={"kind": "reset"}).json()
        assert r["ok"] and time.monotonic() - t0 < 5
        assert svc.kernel.state.session is None
        seen = []
        for _ in range(30):
            f = ws.receive_json()
            seen.append(f)
            if f["type"] == "ui" and f["ui"]["type"] == "reset" and f["priority"] == 0:
                break
        assert seen[-1]["ui"] == {"type": "reset"}
    assert svc.db.one("SELECT COUNT(*) AS n FROM resident")["n"] == 1
    assert any(e["type"] == "operator.reset" for e in _events(svc, "operator."))


def test_panic(client):
    login(client)
    svc = svc_of(client)
    with client.websocket_connect("/ws/device", headers=AUTH) as ws:
        hello(ws)
        ws.receive_json()
        client.post("/operator/api/trigger", json={"kind": "wake"})
        assert _wait(lambda: svc.kernel.state.session is not None)
        assert client.post("/operator/api/override", json={"kind": "panic"}).json()["ok"]
        types = []
        for _ in range(30):
            f = ws.receive_json()
            types.append(f["type"])
            if f["type"] == "stop_all":
                break
        assert "stop_all" in types
    assert svc.kernel.state.session is None


def test_scenario_s2_runs_through_text_loop(client):
    login(client)
    svc = svc_of(client)
    assert client.post("/operator/api/scenario/S2").json()["ok"]
    assert _wait(lambda: _events(svc, "agent.reply_start"), 10)
    assert svc.clock.now().strftime("%H:%M") == "10:00"
    assert client.post("/operator/api/scenario/S9").status_code == 400


def test_outbox_trace_health_fixed(client):
    login(client)
    assert client.get("/operator/api/outbox").json() == {"items": []}
    tr = client.get("/operator/api/trace?limit=5").json()
    assert set(tr) == {"events", "commands", "spans"}
    rows = client.get("/operator/api/health").json()["rows"]
    assert rows and all(r["status"] in ("green", "red", "fake") for r in rows)
    fx = client.get("/operator/api/fixed").json()
    assert "F1" in fx["clips"] and fx["clips"]["F1"]["text"].startswith("哎呀")
    assert client.get("/media/fixed/manifest.json").status_code == 200


def test_sse_stream_sends_state_first(client):
    login(client)
    with client.stream("GET", "/operator/stream") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events = []
        for line in r.iter_lines():
            if line.startswith("event: "):
                events.append(line[7:])
            if "health" in events:
                break
        assert events[:2] == ["state", "health"]


def test_photo_upload(client):
    login(client)
    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 100
    r = client.post("/media/photo", data={"incident_id": "inc_1"}, files={"file": ("p.jpg", jpeg, "image/jpeg")})
    assert r.status_code == 200, r.text
    pid = r.json()["photo_id"]
    assert client.get(f"/media/photo/{pid}").content == jpeg
    bad = client.post("/media/photo", data={"incident_id": "inc_1"}, files={"file": ("p.txt", b"hello", "text/plain")})
    assert bad.status_code == 415
