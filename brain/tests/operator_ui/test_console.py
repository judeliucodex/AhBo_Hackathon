"""Operator console UI tests (GLM task 03): Playwright against a stand-in §9 server with canned data and
an SSE stream, and the REAL face kit in the mirror iframe. Asserts: panels render, buttons POST the right
bodies, the mirror applies `ui` events (mood class on `.face`, cards, caption from play_fixed), Esc = Panic,
state updates mark unacked P0 / incident alarms, and 404 endpoints read "not available yet".

Run in the ahbo-browser image (exact command in docs/handoff/glm/03_REPORT.md).
"""
from __future__ import annotations

import json

from conftest import assert_no_page_errors, frame, post_body


# ---------------------------------------------------------------- panels render
def test_panels_render(console, standin):
    page = console
    standin.push("turn", {"role": "user", "text": "阿寶，我幾時食藥呀？", "session_id": "sess_t", "turn_id": "turn_t1",
                          "stt_confidence": 0.94})
    standin.push("turn", {"role": "ahbo", "text": "兩個鐘之後，十二點正，有四款藥。", "session_id": "sess_t",
                          "turn_id": "turn_t1"})
    # top bar: fake-mode badge (§12.2 labelling) + clock
    page.wait_for_selector("#top-badges .badge.fake")
    assert "fake mode" in page.locator("#top-badges").inner_text()
    assert page.locator("#top-clock").inner_text().startswith("10:00")
    # live state panel: doses table, device row with ack latency + battery, beacon
    page.wait_for_selector("#state-body table")
    state_text = page.locator("#state-body").inner_text()
    assert "s_1200" in state_text and "12:00" in state_text
    assert "42 ms" in state_text and "87%" in state_text
    assert "beacon1" in state_text
    # transcript: her line with STT confidence + Ah Bo's reply
    page.wait_for_selector("#transcript-body .tline.user")
    tr = page.locator("#transcript-body").inner_text()
    assert "我幾時食藥" in tr and "STT 0.94" in tr and "四款藥" in tr
    # outbox: telegram row with its buttons + fake badge; twilio row with press-1
    page.wait_for_selector("#outbox-body .obx")
    ob = page.locator("#outbox-body").inner_text()
    assert "我處理緊" in ob and "fake mode" in ob
    assert page.locator("#outbox-body button", has_text="press 1").count() == 1
    # health table with green/fake statuses
    page.wait_for_selector("#health-body table")
    assert "fake" in page.locator("#health-body").inner_text()
    # knobs: current values highlighted
    assert page.locator("#flash-thinking-seg button.on").inner_text() == "off"
    assert page.locator("#sonnet-thinking-seg button.on").inner_text() == "low"
    # wake panel: only a sim device in the snapshot → the honest "no iPad connected"
    assert "no iPad connected" in page.locator("#wake-body").inner_text()
    # clock panel shows the snapshot time; action log still empty
    assert "10:00" in page.locator("#clock-now").inner_text()
    assert "Nothing sent yet" in page.locator("#actlog-body").inner_text()
    assert_no_page_errors(page)


def test_live_state_marks_incident_and_unacked_p0(console, standin):
    page = console
    standin.snapshot = {**standin.snapshot,
                        "incident": {"id": "inc_live", "kind": "fall", "state": "ESCALATING", "source": "beacon",
                                     "opened_at": "2026-11-20T09:59:00.000+08:00", "timeline": [], "vision_note": None},
                        "devices": [{**standin.snapshot["devices"][0], "unacked_p0": 2, "online": False}]}
    standin.push("state", standin.snapshot)
    page.wait_for_selector("#p-state.alarm")                 # incident box + panel go amber
    page.wait_for_selector("#state-body tr.alarm")           # device row with unacked P0 red
    row = page.locator("#state-body tr.alarm").inner_text()
    assert "2 unacked" in row
    assert "INCIDENT ESCALATING" in page.locator("#top-badges").inner_text()
    assert_no_page_errors(page)


# ---------------------------------------------------------------- triggers POST the right bodies
def test_triggers_post_bodies(console):
    page = console
    assert post_body(page, "#trigger-fall", "/operator/api/trigger") == {"kind": "fall"}
    assert post_body(page, "#trigger-sos", "/operator/api/trigger") == {"kind": "sos"}
    page.select_option("#dose-slot-select", "s_1200")
    assert post_body(page, "#trigger-dose", "/operator/api/trigger") == {"kind": "dose_now", "slot_id": "s_1200"}
    page.select_option("#checkin-select", "afternoon")
    assert post_body(page, "#trigger-checkin", "/operator/api/trigger") == {"kind": "checkin", "checkin": "afternoon"}
    assert post_body(page, "#trigger-voice-note", "/operator/api/trigger") == {"kind": "voice_note", "voice_id": "vm_demo"}
    page.fill("#family-text-input", "媽，記得食飯呀")
    assert post_body(page, "#trigger-family-text", "/operator/api/trigger") == {"kind": "family_text", "text": "媽，記得食飯呀"}
    assert post_body(page, "#trigger-plan", "/operator/api/trigger") == {"kind": "plan_now"}
    assert post_body(page, "#trigger-warmup", "/operator/api/trigger") == {"kind": "warmup"}
    assert post_body(page, "#trigger-birth", "/operator/api/trigger") == {"kind": "birth"}
    assert post_body(page, "#trigger-reset", "/operator/api/reset") == {}
    assert_no_page_errors(page)


def test_scenarios_and_clock(console):
    page = console
    assert post_body(page, "button[data-scenario='S2']", "/scenario/S2") == {}
    assert post_body(page, "button[data-scenario='S4ok']", "/scenario/S4ok") == {}
    assert post_body(page, "button[data-anchor='S1']", "/operator/api/clock") == {"anchor": "S1"}
    page.fill("input.hhmm", "07:58")
    assert post_body(page, "#clock-set", "/operator/api/clock") == {"set": "07:58"}
    page.fill("#clock-offset-input", "90")
    assert post_body(page, "#clock-offset", "/operator/api/clock") == {"offset_s": 90}
    assert post_body(page, "#clock-back-now", "/operator/api/clock") == {"offset_s": 0}
    assert_no_page_errors(page)


def test_overrides_post_bodies(console):
    page = console
    assert post_body(page, "#override-ok", "/operator/api/override") == {"kind": "incident_ok"}
    assert post_body(page, "#override-escalate", "/operator/api/override") == {"kind": "incident_escalate"}
    assert post_body(page, "#override-resolve", "/operator/api/override") == {"kind": "incident_resolve"}
    # force tool result: canned get_time JSON, editable
    page.select_option("#force-tool-select", "get_time")
    canned = json.loads(page.locator("#force-json").input_value())
    assert canned["ok"] is True
    body = post_body(page, "#force-apply", "/operator/api/override")
    assert body == {"kind": "force_tool_result", "tool": "get_time", "result": canned}
    # play a fixed clip (list from GET /operator/api/fixed)
    page.select_option("#clip-select", "F1")
    assert post_body(page, "#override-play-fixed", "/operator/api/override") == {"kind": "play_fixed", "clip_id": "F1"}
    # mute
    assert post_body(page, "#override-mute", "/operator/api/override") == {"kind": "mute"}
    # say a typed line (panel labels it operator)
    page.fill("#say-text", "我叫王婆婆")
    assert post_body(page, "#override-say", "/operator/api/override") == {"kind": "say", "text": "我叫王婆婆"}
    assert "operator" in page.locator("#p-overrides").inner_text()
    # provider fallback
    page.select_option("#provider-select", "llm")
    assert post_body(page, "#override-provider", "/operator/api/override") == {"kind": "provider", "which": "llm", "to": "fallback"}
    # interruption toggle (snapshot says on → posts off)
    assert post_body(page, "#override-interrupt", "/operator/api/override") == {"kind": "interruption", "on": False}
    # knobs
    assert post_body(page, "#flash-thinking-seg button[data-value='low']", "/operator/api/override") == \
        {"kind": "knobs", "flash_thinking": "low"}
    page.wait_for_selector("#flash-thinking-seg button.on[data-value='low']")   # echoed back → seg re-rendered
    assert_no_page_errors(page)


# ---------------------------------------------------------------- live face mirror
def test_mirror_rebuilds_and_applies_ui(console, standin):
    page = console
    # the iframe really is the face kit and starts from the snapshot (mood idle → no mood class)
    assert frame(page) is not None
    fl = frame(page)
    assert "alarm" not in (fl.locator("#face").get_attribute("class") or "")
    # a `ui` SSE event lands in the iframe: mood class on .face
    standin.push("ui", {"type": "mood", "mood": "alarm"})
    fl.wait_for_function("() => document.getElementById('face').classList.contains('alarm')")
    # cards: next_dose + a med chip render inside the mirror
    standin.push("ui", {"type": "card", "card": {"id": "next_dose", "kind": "med", "label": "12:00 食藥"}})
    standin.push("ui", {"type": "card", "card": {"id": "med_bp", "kind": "med", "label": "血壓藥",
                                                 "small": "Amlodipine 5 mg"}})
    fl.wait_for_selector("#cards [data-id='med_bp'] .lbl")
    assert "12:00 食藥" in fl.locator("#cards [data-id='next_dose']").inner_text()
    assert "血壓藥" in fl.locator("#cards [data-id='med_bp']").inner_text()
    # ok_button appears in the mirror (what Po-po can tap)
    standin.push("ui", {"type": "ok_button", "show": True, "label": "我冇事"})
    fl.wait_for_selector("#okbtn")
    assert "我冇事" in fl.locator("#okbtn").inner_text()
    # steps group renders with its states
    standin.push("ui", {"type": "steps", "group": {"id": "fall", "title": "跌倒處理", "steps": [
        {"id": "photo", "label": "影張相", "state": "done"},
        {"id": "ask", "label": "問下你", "state": "active"}]}})
    fl.wait_for_selector("#steps .stepgroup[data-id='fall']")
    assert "跌倒處理" in fl.locator("#steps").inner_text()
    # reset clears the mood class
    standin.push("ui", {"type": "reset"})
    fl.wait_for_function("() => !document.getElementById('face').classList.contains('alarm')")
    assert_no_page_errors(page)


def test_mirror_captions_fixed_clip_from_command(console, standin):
    page = console
    # a device.play_fixed Kernel command → the mirror synthesizes say_start with the clip's text
    standin.push("command", {"id": "01JCMD", "type": "device.play_fixed", "priority": 1,
                             "payload": {"clip_id": "F1", "say_id": "say_t1"}})
    fl = frame(page)
    fl.wait_for_function("() => document.getElementById('caption').textContent.includes('王婆婆')", timeout=8000)
    assert "王婆婆，我係阿寶" in fl.locator("#caption").inner_text()
    assert_no_page_errors(page)


# ---------------------------------------------------------------- outbox presses
def test_outbox_press(console):
    page = console
    assert post_body(page, "#outbox-body button:has-text('我處理緊')", "/outbox/ob_t1/press") == {"button": "handled"}
    assert post_body(page, "#outbox-body button:has-text('press 1')", "/outbox/ob_c1/press") == {"button": "press1"}
    assert_no_page_errors(page)


# ---------------------------------------------------------------- Esc = panic (§12.2)
def test_esc_triggers_panic(console):
    page = console
    with page.expect_request(lambda r: r.method == "POST" and r.url.endswith("/operator/api/panic"), timeout=8000):
        page.keyboard.press("Escape")
    page.wait_for_selector(".toast.panic")
    # the hint is visible in the header without scrolling
    assert "Esc" in page.locator(".panic-hint").inner_text()
    assert_no_page_errors(page)


def test_panic_button(console):
    page = console
    with page.expect_request(lambda r: r.method == "POST" and r.url.endswith("/operator/api/panic"), timeout=8000):
        page.click("#panic-btn")
    page.wait_for_selector(".toast.panic")
    assert_no_page_errors(page)


# ---------------------------------------------------------------- trace view
def test_trace_view(console):
    page = console
    page.fill("#trace-body input", "turn_t1")
    with page.expect_request(lambda r: "/operator/api/trace" in r.url, timeout=8000):
        page.click("#trace-fetch")
    page.wait_for_selector("#trace-body table")
    text = page.locator("#trace-body").inner_text()
    assert "user.utterance" in text and "device.ui" in text and "tool.get_med_status" in text
    assert_no_page_errors(page)


# ---------------------------------------------------------------- 404 → "not available yet"
def test_unavailable_endpoints_read_not_available_yet(page, standin):
    standin.hide.add("/operator/api/health")
    standin.hide.add("/operator/api/outbox")
    page.goto(standin.url + "/operator/")
    page.wait_for_selector("#conn[data-state='open']")
    page.wait_for_selector("#health-body p.na")
    assert "not available yet" in page.locator("#health-body").inner_text()
    page.wait_for_selector("#outbox-body p.na")
    assert "not available yet" in page.locator("#outbox-body").inner_text()
    # the console stays alive: trigger buttons still render
    assert page.locator("#trigger-fall").is_visible()
    assert_no_page_errors(page)
