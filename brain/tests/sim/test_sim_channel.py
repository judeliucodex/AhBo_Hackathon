"""Browser-iPad protocol tests: the sim page against the in-repo stub server (CONTRACTS §2, §3.3).

The stub plays the device hub exactly as `brain/ahbo/links/device_hub.py` does for the parts the sim
speaks: hello → welcome (+ replay), commands needing acks, and it records every sim → server frame.
Run inside the ahbo-browser image, or from ahbo-api with AHBO_BROWSER_WS pointing at the
`playwright run-server` of an ahbo-browser container (exact commands in docs/handoff/glm/05_REPORT.md).
"""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.browser

from sim_harness import (count_trace, face_frame, wait_online, wait_online_off, wait_stub,  # noqa: E402
                         wait_trace)


async def _wait_count(locator, n: int, msg: str, timeout_s: float = 4.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while await locator.count() != n:
        if loop.time() > deadline:
            raise AssertionError(f"{msg} (count={await locator.count()}, want {n})")
        await asyncio.sleep(0.05)


async def _wait_disabled(locator, want: bool, msg: str, timeout_s: float = 4.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while await locator.is_disabled() != want:
        if loop.time() > deadline:
            raise AssertionError(msg)
        await asyncio.sleep(0.05)


# ------------------------------------------------------------------ hello / welcome / replay
async def test_hello_welcome_and_replay(sim_page):
    page, stub = sim_page
    hello = stub.hub.frames[0]
    assert hello["type"] == "hello"
    assert hello["device_id"].startswith("sim-")
    assert hello["wake_engine"] == "none"
    assert isinstance(hello["app_version"], str) and hello["app_version"]
    assert isinstance(hello["fixed_cache_version"], str)

    welcome = await wait_trace(page, "e.dir === 'in' && e.obj.type === 'welcome'")
    assert welcome["obj"]["text_sessions"] is True
    assert welcome["obj"]["fixed_cache_version"] == "f-stub0001"
    assert "quiet_hours" in welcome["obj"]["config"]

    ready = await wait_trace(page, "e.dir === 'out' && e.obj.type === 'face_event'"
                                   " && e.obj.event && e.obj.event.type === 'ready'")
    assert ready["obj"]["event"]["version"]

    # The stub's replay (reset + mood alarm) reached the face: it is in the alarm mood.
    face = face_frame(page)
    await face.locator("#face.alarm").wait_for(state="attached", timeout=4000)
    assert await page.locator("#devId").inner_text() == hello["device_id"]


# ------------------------------------------------------------------ ui commands → iframe
async def test_ui_commands_reach_the_face(sim_page):
    page, stub = sim_page
    face = face_frame(page)
    await stub.hub.send_ui({"type": "mood", "mood": "thinking"})
    await face.locator("#face.thinking").wait_for(state="attached", timeout=4000)

    await stub.hub.send_ui({"type": "card", "card": {"id": "next_dose", "kind": "event", "icon": "⏰",
                                                     "label": "12:00 食藥", "small": "兩個鐘之後"}})
    chip = face.locator("[data-id='next_dose']")
    await chip.wait_for(state="visible", timeout=4000)
    assert "12:00 食藥" in await chip.inner_text()

    await stub.hub.send_ui({"type": "steps", "group": {"id": "t1", "steps": [
        {"id": "s1", "label": "查緊藥物時間表…", "state": "active"}]}})
    await face.locator(".stepgroup[data-id='t1'] .step.active").wait_for(state="visible", timeout=4000)

    await stub.hub.send_ui({"type": "ok_button", "show": True})
    await face.locator("#okbtn.on").wait_for(state="visible", timeout=4000)

    await stub.hub.send_ui({"type": "heard", "text": "阿寶，我幾時食藥呀？", "final": True})
    await face.locator("#heard.on").wait_for(state="attached", timeout=4000)

    await stub.hub.send_ui({"type": "reset"})
    await _wait_count(face.locator("#cards .chip"), 0, "cards not cleared by reset")


# ------------------------------------------------------------------ acks + heartbeat
async def test_every_command_is_acked_and_heartbeat_flows(sim_page):
    page, stub = sim_page
    await stub.hub.send_ui({"type": "mood", "mood": "curious"})
    await stub.hub.send_command({"type": "play_fixed", "priority": 1, "clip_id": "NOPE",
                                 "say_id": "say_ack"})

    deadline = asyncio.get_running_loop().time() + 4
    while len(stub.hub.of_type("ack")) < 2:
        assert asyncio.get_running_loop().time() < deadline, "commands were not acked"
        await asyncio.sleep(0.05)
    acks = stub.hub.of_type("ack")
    assert all(f["ok"] is True and isinstance(f["ref"], str) for f in acks)

    deadline = asyncio.get_running_loop().time() + 4
    while len(stub.hub.of_type("heartbeat")) < 3:
        assert asyncio.get_running_loop().time() < deadline, f"heartbeats: {len(stub.hub.of_type('heartbeat'))} in 4 s (hb_ms=300)"
        await asyncio.sleep(0.05)
    b = stub.hub.of_type("heartbeat")[0]
    assert 0 <= b["battery"] <= 1 and b["thermal"] == "nominal" and isinstance(b["in_session"], bool)


# ------------------------------------------------------------------ play_fixed (real audio)
async def test_play_fixed_with_audio_full_cycle(sim_page):
    page, stub = sim_page
    await page.locator("#sound").click()          # user gesture: unlocks playback in real browsers
    await stub.hub.send_command({"type": "play_fixed", "priority": 0, "clip_id": "F1",
                                 "say_id": "say_f1"})

    start = await wait_trace(page, "e.dir === 'face' && e.obj.type === 'say_start'"
                                   " && e.obj.id === 'say_f1'")
    assert start["obj"]["text"] == "哎呀！我感應到你好似跌低咗！"
    assert start["obj"]["segments"] == [[0, 14, 0.0, 0.9]]

    end = await wait_trace(page, "e.dir === 'face' && e.obj.type === 'say_end'"
                                 " && e.obj.id === 'say_f1'", timeout_s=8)
    span_s = (end["t"] - start["t"]) / 1000
    assert 0.6 <= span_s <= 6, f"caption window {span_s:.2f}s, want ~0.9s (the wav's length)"

    done = await wait_stub(stub, lambda f: f["type"] == "playback_done" and f.get("say_id") == "say_f1")
    assert done["clip_id"] == "F1" and "error" not in done


# ------------------------------------------------------------------ play_fixed (file missing → timer)
async def test_play_fixed_missing_file_still_finishes(sim_page):
    page, stub = sim_page
    await stub.hub.send_command({"type": "play_fixed", "priority": 1, "clip_id": "F2",
                                 "say_id": "say_f2"})

    start = await wait_trace(page, "e.dir === 'face' && e.obj.type === 'say_start'"
                                   " && e.obj.id === 'say_f2'")
    assert start["obj"]["segments"] == [[0, 10, 0.0, 1.2]]      # face driven from the manifest anyway

    end = await wait_trace(page, "e.dir === 'face' && e.obj.type === 'say_end'"
                                 " && e.obj.id === 'say_f2'", timeout_s=8)
    span_s = (end["t"] - start["t"]) / 1000
    assert 0.9 <= span_s <= 6, f"fallback timer ran {span_s:.2f}s, want ~1.2s (manifest duration)"

    done = await wait_stub(stub, lambda f: f["type"] == "playback_done" and f.get("say_id") == "say_f2")
    assert done["clip_id"] == "F2" and done.get("error")       # failed playback is reported, not hidden


# ------------------------------------------------------------------ play_voice_message (family)
async def test_play_voice_message_family(sim_page):
    page, stub = sim_page
    await page.locator("#sound").click()
    await stub.hub.send_command({"type": "play_voice_message", "priority": 2, "message_id": "vm_1",
                                 "url": "/media/voice/tone.wav", "say_id": "say_vm1",
                                 "from_name": "阿珍", "text": "媽，記得食飯呀！", "duration_s": 2.0})

    start = await wait_trace(page, "e.dir === 'face' && e.obj.type === 'say_start'"
                                   " && e.obj.id === 'say_vm1'")
    assert start["obj"]["from"] == "family"
    assert start["obj"]["fromName"] == "阿珍"
    assert start["obj"]["durationSec"] == 2.0

    face = face_frame(page)
    await face.locator("#caption.family").wait_for(state="visible", timeout=3000)
    assert await face.locator("#caption").get_attribute("data-from") == "阿珍"

    await wait_trace(page, "e.dir === 'face' && e.obj.type === 'say_end' && e.obj.id === 'say_vm1'",
                     timeout_s=8)
    done = await wait_stub(stub, lambda f: f["type"] == "playback_done" and f.get("say_id") == "say_vm1")
    assert done["message_id"] == "vm_1"


# ------------------------------------------------------------------ session + text box + utterance
async def test_session_opens_text_box_and_utterance_flows(sim_page):
    page, stub = sim_page
    assert await page.locator("#sayText").is_disabled()

    await stub.hub.send_command({"type": "session.open", "priority": 1, "reason": "reminder",
                                 "mode": "companion", "session_id": "sess_t1", "text_only": True})
    await _wait_disabled(page.locator("#sayText"), False, "text box not enabled by session.open")
    assert "sess_t1" in await page.locator("#sess").inner_text()

    await page.locator("#sayText").fill("阿寶，我幾時食藥呀？")
    await page.locator("#sayBtn").click()
    utt = await wait_stub(stub, lambda f: f["type"] == "utterance")
    assert utt["text"] == "阿寶，我幾時食藥呀？" and utt["final"] is True

    await stub.hub.send_command({"type": "session.close", "priority": 1, "session_id": "sess_t1"})
    await _wait_disabled(page.locator("#sayText"), True, "text box not disabled by session.close")
    assert (await page.locator("#sess").inner_text()) == "none"


# ------------------------------------------------------------------ touch_ok (panel + face button)
async def test_touch_ok_from_panel_and_face_button(sim_page):
    page, stub = sim_page
    await page.locator("#okBtn").click()
    await wait_stub(stub, lambda f: f["type"] == "touch_ok")

    face = face_frame(page)
    await stub.hub.send_ui({"type": "ok_button", "show": True})
    await face.locator("#okbtn.on").wait_for(state="visible", timeout=4000)
    await face.locator("#okbtn").click()
    await wait_stub(stub, lambda f: f["type"] == "touch_ok", timeout_s=4)
    assert len(stub.hub.of_type("touch_ok")) == 2


# ------------------------------------------------------------------ face tap = wake while idle
async def test_face_tap_wakes_only_when_idle(sim_page):
    page, stub = sim_page
    await face_frame(page).locator("#stage").click()
    await wait_trace(page, "e.dir === 'face' && e.obj.type === 'expr' && e.obj.expr === 'wake'")
    wake = await wait_stub(stub, lambda f: f["type"] == "wake")
    assert wake["trigger"] == "touch"

    # With a session open, the same tap is only forwarded for the trace — no second wake.
    await stub.hub.send_command({"type": "session.open", "priority": 1, "reason": "reminder",
                                 "mode": "companion", "session_id": "sess_t2", "text_only": True})
    deadline = asyncio.get_running_loop().time() + 4
    while "sess_t2" not in await page.locator("#sess").inner_text():
        assert asyncio.get_running_loop().time() < deadline, "session.open did not land"
        await asyncio.sleep(0.05)
    n_wakes = len(stub.hub.of_type("wake"))
    await face_frame(page).locator("#stage").click()
    tap_event = await wait_stub(stub, lambda f: f["type"] == "face_event"
                                and f.get("event", {}).get("type") == "tap")
    assert tap_event["event"]["target"] == "face"
    assert len(stub.hub.of_type("wake")) == n_wakes, "a face tap during a session must not wake"


# ------------------------------------------------------------------ capture_photo → upload
async def test_capture_photo_uploads_placeholder_jpeg(sim_page):
    page, stub = sim_page
    assert not await page.locator("#camera").is_checked()      # no permission prompt mid-incident
    await stub.hub.send_command({"type": "capture_photo", "priority": 0, "incident_id": "inc_t1"})

    loop = asyncio.get_running_loop()
    deadline = loop.time() + 8
    while not stub.photo_uploads:
        assert loop.time() < deadline, "no photo upload happened"
        await asyncio.sleep(0.1)
    up = stub.photo_uploads[0]
    assert up["incident_id"] == "inc_t1" and up["jpeg"] and up["size"] > 1000

    done = await wait_stub(stub, lambda f: f["type"] == "photo_uploaded")
    assert done["photo_id"] == "ph_stub0001" and done["incident_id"] == "inc_t1"


# ------------------------------------------------------------------ dedupe of resent commands
async def test_duplicate_command_runs_once(sim_page):
    page, stub = sim_page
    cid = await stub.hub.send_command_twice({"type": "play_fixed", "priority": 0, "clip_id": "F2",
                                             "say_id": "say_dup"})
    deadline = asyncio.get_running_loop().time() + 4
    while len([f for f in stub.hub.of_type("ack") if f["ref"] == cid]) < 2:
        assert asyncio.get_running_loop().time() < deadline, "resent command was not acked twice"
        await asyncio.sleep(0.05)

    await wait_stub(stub, lambda f: f["type"] == "playback_done" and f.get("say_id") == "say_dup",
                    timeout_s=8)
    starts = await count_trace(page, "e.dir === 'face' && e.obj.type === 'say_start'"
                                     " && e.obj.id === 'say_dup'")
    assert starts == 1, "a duplicated command must be acked, never run twice"


# ------------------------------------------------------------------ stop_all
async def test_stop_all_stops_and_resets(sim_page):
    page, stub = sim_page
    await page.locator("#sound").click()
    await stub.hub.send_command({"type": "play_fixed", "priority": 0, "clip_id": "F1",
                                 "say_id": "say_stop"})
    await wait_trace(page, "e.dir === 'face' && e.obj.type === 'say_start' && e.obj.id === 'say_stop'")

    await stub.hub.send_command({"type": "stop_all", "priority": 0})
    done = await wait_stub(stub, lambda f: f["type"] == "playback_done" and f.get("say_id") == "say_stop",
                           timeout_s=8)
    assert done.get("error") == "stopped"
    await wait_trace(page, "e.dir === 'face' && e.obj.type === 'reset'")


# ------------------------------------------------------------------ wake.enable / config.update /
# voice-session toggle / unknown command (the rest of CONTRACTS §2.2)
async def test_wake_enable_config_update_and_voice_session_toggle(sim_page):
    page, stub = sim_page
    await stub.hub.send_command({"type": "wake.enable", "priority": 0, "on": False})
    ack = await wait_stub(stub, lambda f: f["type"] == "ack")
    assert ack["ok"] is True

    before = stub.manifest_requests
    await stub.hub.send_command({"type": "config.update", "priority": 2,
                                 "fixed_cache_version": "f-stub0002",
                                 "quiet_hours": {"start": "23:00", "end": "07:00"}})
    deadline = asyncio.get_running_loop().time() + 4
    while (await page.locator("#clipVer").inner_text()) != "f-stub0002":
        assert asyncio.get_running_loop().time() < deadline, "clip version not updated by config.update"
        await asyncio.sleep(0.05)
    assert stub.manifest_requests > before, "a new fixed_cache_version must refetch the manifest"

    # A voice session (LiveKit not faked) shows the "Talk (browser mic)" toggle; text stays locked.
    await stub.hub.send_command({"type": "session.open", "priority": 1, "reason": "touch",
                                 "mode": "companion", "session_id": "sess_v1", "text_only": False})
    deadline = asyncio.get_running_loop().time() + 4
    while "sess_v1" not in await page.locator("#sess").inner_text():
        assert asyncio.get_running_loop().time() < deadline, "voice session.open did not land"
        await asyncio.sleep(0.05)
    assert await page.locator("#talkBox").is_visible(), "voice session must show the Talk toggle"
    assert await page.locator("#sayText").is_disabled()
    await stub.hub.send_command({"type": "session.close", "priority": 1, "session_id": "sess_v1"})
    deadline = asyncio.get_running_loop().time() + 4
    while await page.locator("#talkBox").is_visible():
        assert asyncio.get_running_loop().time() < deadline, "talk toggle not hidden after session.close"
        await asyncio.sleep(0.05)

    # An unknown command type is still acked (negatively), never silently dropped.
    await stub.hub.send_command({"type": "not_a_real_command", "priority": 3})
    bad = await wait_stub(stub, lambda f: f["type"] == "ack" and f.get("ok") is False)
    assert bad["error"] == "unknown_command"


# ------------------------------------------------------------------ face error events forwarded
async def test_face_error_events_are_forwarded(sim_page):
    page, stub = sim_page
    await stub.hub.send_ui({"type": "mood", "mood": "nope"})        # the face rejects this
    err = await wait_stub(stub, lambda f: f["type"] == "face_event"
                          and f.get("event", {}).get("type") == "error", timeout_s=4)
    assert "unknown mood" in err["event"]["message"]


# ------------------------------------------------------------------ photo upload failure → error frame
async def test_photo_upload_failure_reports_error(sim_page):
    page, stub = sim_page
    stub.fail_photo = True
    await stub.hub.send_command({"type": "capture_photo", "priority": 0, "incident_id": "inc_fail"})
    frame = await wait_stub(stub, lambda f: f["type"] == "error", timeout_s=8)
    assert frame["code"] == "camera_failed" and "500" in frame["detail"]
    assert not [f for f in stub.hub.of_type("photo_uploaded")], "no photo_uploaded after a failed upload"


# ------------------------------------------------------------------ reconnect + offline dot
async def test_reconnect_with_backoff_and_offline_dot(page, stub):
    await page.goto(f"{stub.url}/sim/?hb_ms=300&offline_ms=1200")
    await wait_online(page)
    hub = stub.hub
    assert hub.hellos == 1

    # 1) a clean drop → reconnect with backoff
    await hub.drop()
    await wait_online_off(page)
    deadline = asyncio.get_running_loop().time() + 8
    while hub.hellos < 2:
        assert asyncio.get_running_loop().time() < deadline, "no reconnect after a drop"
        await asyncio.sleep(0.1)
    await wait_online(page)

    # 2) every attempt rejected (like an expired cookie) → after ~1.2 s the face shows the offline dot
    hub.reject_ws = True
    await hub.drop()
    face = face_frame(page)
    await face.locator("#offline.on").wait_for(state="visible", timeout=6000)

    # 3) accepted again → the dot clears on the next welcome
    hub.reject_ws = False
    deadline = asyncio.get_running_loop().time() + 16
    while hub.hellos < 3:
        assert asyncio.get_running_loop().time() < deadline, "no reconnect after un-rejecting"
        await asyncio.sleep(0.2)
    await wait_online(page, timeout_s=10)
    deadline = asyncio.get_running_loop().time() + 8
    while await face.locator("#offline.on").count() > 0:
        assert asyncio.get_running_loop().time() < deadline, "offline dot still on after reconnect"
        await asyncio.sleep(0.1)


# ------------------------------------------------------------------ QA 25 D2 / D11: silent loss + hanging connects
async def _offline_dot(face, want: bool, timeout_s: float) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while (await face.locator("#offline.on").count() > 0) != want:
        assert asyncio.get_running_loop().time() < deadline, f"offline dot never {'on' if want else 'off'}"
        await asyncio.sleep(0.05)


async def test_silent_network_loss_is_detected_by_the_pong_watchdog(page, stub):
    await page.goto(f"{stub.url}/sim/?hb_ms=300&offline_ms=1200")
    await wait_online(page)
    await wait_trace(page, "e.dir === 'in' && e.obj.type === 'pong'")          # the hub answers heartbeats
    hub = stub.hub
    hub.silent = True              # the path dies silently: no frames reach the sim, the socket never closes
    await wait_online_off(page, timeout_s=3)                                  # dead after ~2.5 heartbeats
    face = face_frame(page)
    await _offline_dot(face, True, 4)                                         # offline_ms after the last frame
    hellos = hub.hellos
    hub.silent = False                                                        # the network is back
    await wait_online(page, timeout_s=10)
    assert hub.hellos > hellos
    await _offline_dot(face, False, 3)


async def test_hanging_connect_attempt_is_abandoned_and_retried(page, stub):
    await page.goto(f"{stub.url}/sim/?hb_ms=300&offline_ms=1200")
    await wait_online(page)
    hub = stub.hub
    hub.hang_upgrade = True                    # the server is half up: upgrades never answer
    await hub.drop()
    face = face_frame(page)
    await _offline_dot(face, True, 6)
    deadline = asyncio.get_running_loop().time() + 8
    while hub.hangs < 2:                       # the first hung attempt was given up and a new one made
        assert asyncio.get_running_loop().time() < deadline, f"hung attempts: {hub.hangs}"
        await asyncio.sleep(0.1)
    hub.hang_upgrade = False                   # the server is back: the next attempt (≤ 2 s timeout + backoff)
    await wait_online(page, timeout_s=8)
    await _offline_dot(face, False, 3)
