"""Device channel (CONTRACTS §2) through the real app and Starlette's TestClient websockets."""
from __future__ import annotations

import time

import pytest
from platform_app import APP_SECRET, drain_until, hello, login, svc_of
from starlette.websockets import WebSocketDisconnect

from ahbo.api.auth import check_device_token
from ahbo.core.events import P0, P2, cmd, ui
from ahbo.links.device_hub import Conn, Outgoing, _step_keys, command_to_frame

AUTH = {"Authorization": f"Bearer {APP_SECRET}"}


def emit(c, *cmds):
    async def go():
        await svc_of(c).kernel.emit(*cmds)
    c.portal.call(go)


def test_refused_without_auth(client):
    with pytest.raises(WebSocketDisconnect) as e, client.websocket_connect("/ws/device") as ws:
        ws.receive_json()
    assert e.value.code == 4401
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/ws/device", headers={"Authorization": "Bearer wrong"}) as ws:
            ws.receive_json()
    assert e.value.code == 4401


def test_cookie_needs_sim_flag(client):
    login(client)
    # TestClient's websocket URL is ws:// so it won't send the Secure cookie by itself (browsers use wss://)
    cookie = {"cookie": f"ahbo_session={client.cookies['ahbo_session']}"}
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/ws/device", headers=cookie) as ws:
            ws.receive_json()
    assert e.value.code == 4401
    with client.websocket_connect("/ws/device?sim=1", headers=cookie) as ws:
        w = hello(ws, "sim-abc")
        assert w["type"] == "welcome"


def test_hello_welcome_and_replay(client):
    emit(client, ui({"type": "mood", "mood": "warm"}), ui({"type": "card", "card": {
        "id": "today", "kind": "today", "label": "今日", "persistent": True}}))
    with client.websocket_connect("/ws/device", headers=AUTH) as ws:
        w = hello(ws)
        assert w["type"] == "welcome"
        assert check_device_token(svc_of(client).settings, w["device_token"]) == "ipad-test-1"
        assert w["text_sessions"] is True
        assert w["server_time"].endswith("+08:00")
        assert w["fixed_cache_version"].startswith("f-")
        cfg = w["config"]
        assert cfg["quiet_hours"] == {"start": "22:30", "end": "07:00"}
        assert cfg["wake"]["require_wai"] is True and "stage3_timeout_s" not in cfg["wake"]
        mirror = cfg["reminder_mirror"]
        assert mirror["date"] == "2026-11-20"
        s1200 = next(s for s in mirror["slots"] if s["slot_id"] == "s_1200")
        assert s1200["clip_id"] == "R_s_1200" and s1200["time"] == "12:00"
        assert s1200["cards"][0] == {"id": "med_bp", "kind": "med", "label": "血壓藥", "small": "Amlodipine 5 mg",
                                     "pill": ["--pill-b", "--pill-a"]}
        frames = [ws.receive_json() for _ in range(3)]
        assert [f["ui"]["type"] for f in frames] == ["reset", "mood", "card"]
        assert all(f["type"] == "ui" and len(f["id"]) == 26 for f in frames)
        # face reload → replay again
        ws.send_json({"id": "x1", "type": "face_event", "event": {"type": "ready", "version": "1"}})
        frames = [ws.receive_json() for _ in range(3)]
        assert frames[0]["ui"]["type"] == "reset" and frames[1]["ui"] == {"type": "mood", "mood": "warm"}
        row = svc_of(client).db.one("SELECT * FROM devices WHERE id='ipad-test-1'")
        assert row["online"] == 1 and row["wake_engine"] == "speech_transcriber"


def test_commands_broadcast_and_ack(client):
    svc = svc_of(client)
    with client.websocket_connect("/ws/device", headers=AUTH) as a, \
            client.websocket_connect("/ws/device", headers=AUTH) as b:
        hello(a, "ipad-a")
        a.receive_json(), a.receive_json()   # replay: reset + mood (CONTRACTS §2.4)
        hello(b, "ipad-b")
        b.receive_json(), b.receive_json()   # replay: reset + mood (CONTRACTS §2.4)
        c = cmd("device.play_fixed", P0, clip_id="F1", say_id="say_x_F1")
        emit(client, c)
        fa, fb = a.receive_json(), b.receive_json()
        assert fa == fb == {"id": c.id.split("_", 1)[1], "type": "play_fixed", "priority": 0, "clip_id": "F1",
                            "say_id": "say_x_F1"}
        a.send_json({"id": "k1", "type": "ack", "ref": fa["id"], "ok": True})
        b.send_json({"id": "k2", "type": "ack", "ref": fb["id"], "ok": True})
        for _ in range(50):
            row = svc.db.one("SELECT status FROM commands WHERE id=?", (c.id,))
            if row["status"] == "acked":
                break
            time.sleep(0.02)
        assert row["status"] == "acked"
        time.sleep(0.1)
        acks = svc.db.query("SELECT payload FROM events WHERE type='device.ack'")
        assert len(acks) == 1            # first ack counts
        snap = {d["device_id"]: d for d in svc.hub.snapshot()}
        assert snap["ipad-a"]["online"] and snap["ipad-a"]["ack_latency_ms"] is not None


def test_p0_resend_then_red(client, monkeypatch):
    import ahbo.links.device_hub as dh
    monkeypatch.setattr(dh, "ACK_TIMEOUT_S", 0.15)
    svc = svc_of(client)
    with client.websocket_connect("/ws/device", headers=AUTH) as ws:
        hello(ws)
        ws.receive_json(), ws.receive_json()   # replay: reset + mood (CONTRACTS §2.4)
        c = cmd("device.capture_photo", P0, incident_id="inc_1")
        emit(client, c)
        ids = [ws.receive_json()["id"] for _ in range(4)]     # original + 3 resends, same id
        assert len(set(ids)) == 1
        for _ in range(100):
            if svc.db.one("SELECT status FROM commands WHERE id=?", (c.id,))["status"] == "failed":
                break
            time.sleep(0.02)
        assert svc.db.one("SELECT status, detail FROM commands WHERE id=?", (c.id,)) == \
            {"status": "failed", "detail": "no_ack"}
        assert svc.hub.snapshot()[0]["unacked_p0"] == 1


def test_frame_mapping():
    c = cmd("device.play_voice_message", P2, message_id="vm_demo", url="/media/voice/vm_demo", say_id="s1",
            from_name="阿珍", text="媽", duration_s=4.1)
    f = command_to_frame(c)
    assert f["type"] == "play_voice_message" and f["message_id"] == "vm_demo" and "id_" not in f
    assert command_to_frame(cmd("device.session_open", 1, session_id="sess_1", reason="wake", mode="companion",
                                text_only=True))["type"] == "session.open"
    stop = cmd("device.stop_all", P0)
    assert command_to_frame(stop) == {"id": stop.id.split("_", 1)[1], "type": "stop_all", "priority": 0}
    assert command_to_frame(cmd("family.send", 1)) is None
    assert _step_keys({"type": "step", "group": "g", "id": "a", "state": "done"}) == ([], ("g", "a"))
    assert _step_keys({"type": "steps", "group": {"id": "g", "steps": [{"id": "a", "state": "active"},
                                                                       {"id": "b"}]}}) == ([("g", "a")], None)


async def test_p0_drops_queued_non_p0_ui():
    class W:
        pass
    conn = Conn(ws=W(), sim=False)
    conn.enqueue(Outgoing({"id": "1", "type": "ui", "ui": {"type": "card"}}, 2, True))
    conn.enqueue(Outgoing({"id": "2", "type": "play_fixed"}, 2, False))
    conn.enqueue(Outgoing({"id": "3", "type": "ui", "ui": {"type": "mood"}}, 0, True))
    conn.enqueue(Outgoing({"id": "4", "type": "ui", "ui": {"type": "reset"}}, 0, True))
    assert [x.frame["id"] for x in conn.queue] == ["2", "3", "4"]


def test_step_done_held_600ms_and_order_kept(client):
    with client.websocket_connect("/ws/device", headers=AUTH) as ws:
        hello(ws)
        ws.receive_json(), ws.receive_json()   # replay: reset + mood (CONTRACTS §2.4)
        group = {"id": "turn_1", "steps": [{"id": "a", "label": "睇緊", "state": "active"}]}
        emit(client, ui({"type": "steps", "group": group}), ui({"type": "step", "group": "turn_1", "id": "a",
                                                                 "state": "done"}),
             ui({"type": "mood", "mood": "warm"}))
        t0 = time.monotonic()
        f1 = ws.receive_json()
        f2 = ws.receive_json()
        t2 = time.monotonic()
        f3 = ws.receive_json()
        assert f1["ui"]["type"] == "steps" and f2["ui"]["type"] == "step" and f3["ui"]["type"] == "mood"
        assert t2 - t0 >= 0.5


def test_device_events_become_kernel_events(client):
    svc = svc_of(client)
    with client.websocket_connect("/ws/device", headers=AUTH) as ws:
        hello(ws)
        ws.receive_json(), ws.receive_json()   # replay: reset + mood (CONTRACTS §2.4)
        ws.send_json({"id": "e1", "type": "playback_done", "clip_id": "F1", "say_id": "say_dup"})
        ws.send_json({"id": "e2", "type": "playback_done", "clip_id": "F1", "say_id": "say_dup"})
        ws.send_json({"id": "e3", "type": "heartbeat", "battery": 0.8, "thermal": "nominal", "in_session": False})
        ws.send_json({"id": "e4", "type": "touch_ok"})
        ws.send_json({"id": "e5", "type": "photo_uploaded", "photo_id": "ph_1", "incident_id": "inc_1"})
        ws.send_json({"id": "e6", "type": "face_event", "event": {"type": "say_done", "id": "x"}})
        time.sleep(0.4)
        types = [r["type"] for r in svc.db.query("SELECT type FROM events ORDER BY rowid")]
        assert types.count("device.playback_done") == 1
        for t in ("device.heartbeat", "device.touch_ok", "device.photo_uploaded", "device.face_event",
                  "device.online"):
            assert t in types, t
        row = svc.db.one("SELECT battery, thermal FROM devices WHERE id='ipad-test-1'")
        assert row == {"battery": 0.8, "thermal": "nominal"}


def test_utterance_runs_text_session(client):
    """S2 through the device channel: the reply reaches the device as face commands."""
    with client.websocket_connect("/ws/device", headers=AUTH) as ws:
        hello(ws)
        ws.receive_json(), ws.receive_json()   # replay: reset + mood (CONTRACTS §2.4)
        ws.send_json({"id": "u1", "type": "utterance", "text": "阿寶，我幾時食藥呀？", "final": True})
        got = drain_until(ws, lambda f: f.get("type") == "ui" and f["ui"].get("type") == "say_start")
        types = [f["type"] for f in got]
        assert "session.open" in types
        say = got[-1]["ui"]
        assert "十二點" in say["text"]
        cards = [f["ui"]["card"]["id"] for f in got if f["type"] == "ui" and f["ui"]["type"] == "card"]
        assert "next_dose" in cards or any(x.startswith("med_") for x in cards)


def test_offline_after_disconnect(client, monkeypatch):
    import ahbo.links.device_hub as dh
    monkeypatch.setattr(dh, "OFFLINE_AFTER_S", 0.2)
    svc = svc_of(client)
    with client.websocket_connect("/ws/device", headers=AUTH) as ws:
        hello(ws, "ipad-off")
        ws.receive_json(), ws.receive_json()   # replay: reset + mood (CONTRACTS §2.4)
    time.sleep(0.6)
    assert svc.db.one("SELECT online FROM devices WHERE id='ipad-off'")["online"] == 0
    assert svc.db.one("SELECT COUNT(*) AS n FROM events WHERE type='device.offline'")["n"] == 1


def test_no_device_marks_dropped(client):
    svc = svc_of(client)
    c = cmd("device.wake_enable", 1, on=True)
    emit(client, c)
    assert svc.db.one("SELECT status FROM commands WHERE id=?", (c.id,))["status"] == "dropped"
