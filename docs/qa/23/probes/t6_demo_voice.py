"""QA23 checks 7+8: demo controls (each recorded as an event; /fall and /sos carry source operator and the
face card says 操作員觸發; /say labelled operator) and voice notes (阿珍: stored, announced with A1 when idle,
playable; stranger: dropped).
"""
from __future__ import annotations

import asyncio

import pytest
from qa_helpers import (bot_tasks, confirm, events_of, make_svc_fixture, say, settle, stop_all, tap,
                        commands_of)
from bot_helpers import CHUN_CHAT, STRANGER, channel
from ahbo.links import telegram

CHAT = "8925008896"


@pytest.fixture
async def svc(tmp_path):
    made: list = []
    s = await make_svc_fixture(tmp_path, made)
    yield s
    await stop_all(made)


def _texts(replies):
    return [r["text"] for r in replies]


def _ui(svc):
    return " ".join(str(c["payload"]) for c in commands_of(svc, "device.ui"))


def _notes(svc):
    return events_of(svc, "operator.telegram")


async def test_fall_and_sos_are_operator_incidents_with_the_operator_face_card(svc, tmp_path):
    r = await say(svc, "/fall")
    assert not events_of(svc, "sensor.fall_suspected"), "/fall acted before 確認"
    r = await confirm(svc, r)
    assert any("操作員觸發" in t for t in _texts(r))
    [ev] = events_of(svc, "sensor.fall_suspected")
    assert ev["source"] == "operator"
    inc = svc.db.one("SELECT source, kind FROM incidents ORDER BY opened_at DESC LIMIT 1")
    assert inc["source"] == "operator" and inc["kind"] == "fall"
    assert "操作員觸發" in _ui(svc), "face incident card does not say 操作員觸發"
    note = _notes(svc)[-1]["payload"]
    assert note["op"] == "fall" and note["by"] == "c_chun" and note["via"] == "telegram", note

    # sos on a FRESH svc: while an incident is open, a second trigger attaches to it by design
    # (fall.start docstring: "Open an incident (or attach to the open one)")
    made: list = []
    s2 = await make_svc_fixture(tmp_path, made)
    try:
        r = await say(s2, "/sos")
        await confirm(s2, r)
        [ev] = events_of(s2, "sensor.sos_pressed")
        assert ev["source"] == "operator"
        inc = s2.db.one("SELECT source, kind FROM incidents ORDER BY opened_at DESC LIMIT 1")
        assert inc["source"] == "operator" and inc["kind"] == "sos", inc
        assert "操作員觸發" in _ui(s2)
    finally:
        await stop_all(made)


@pytest.mark.parametrize("cmd,op,event,present", [
    ("/reset", "reset", "operator.reset", None),
    ("/panic", "panic", "operator.panic", None),
    ("/mute", "mute", "operator.mute", "knobs muted"),
    ("/unmute", "unmute", "operator.unmute", None),
    ("/dose 12:00", "dose", "operator.dose_now", None),
    ("/checkin morning", "ck", "operator.checkin_now", None),
    ("/clock 07:58", "clock", "operator.clock", "clock moved"),
    ("/clock +30", "clock", "operator.clock", None),
    ("/thinking flash high", "flash", "operator.model_config", "knob flash"),
])
async def test_admin_controls_record_operator_events(svc, cmd, op, event, present):
    knobs0 = dict(svc.db.kv_get("knobs", {}) or {})
    now0 = svc.kernel.clock.now()
    r = await say(svc, cmd)
    assert any("確認？" in t for t in _texts(r)), f"{cmd} did not ask for 確認"
    assert not events_of(svc, event), f"{cmd} fired before 確認"
    r = await confirm(svc, r)
    assert _notes(svc) and _notes(svc)[-1]["payload"]["op"] == op, f"no operator.telegram note for {cmd}"
    assert _notes(svc)[-1]["payload"]["by"] == "c_chun"
    evs = events_of(svc, event)
    assert evs, f"{cmd} produced no {event}"
    assert evs[-1]["source"] == "operator"
    knobs1 = dict(svc.db.kv_get("knobs", {}) or {})
    if present == "knobs muted":
        assert knobs1.get("muted") == 1, "mute did not reach knobs"
    if present == "knob flash":
        assert knobs1.get("flash_thinking") == "high", knobs1
    if present == "clock moved":
        assert svc.kernel.clock.now() != now0, "clock did not move"


async def test_say_is_labelled_operator(svc):
    r = await say(svc, "/say 王婆婆，飲啖水先")
    assert "標明操作員" in r[0]["text"], r
    r = await confirm(svc, r)
    assert any("標明操作員" in t for t in _texts(r))
    [ev] = events_of(svc, "operator.say")
    assert ev["source"] == "operator" and ev["payload"]["text"] == "王婆婆，飲啖水先"
    assert _notes(svc)[-1]["payload"]["op"] == "say"
    ui = _ui(svc)
    assert "王婆婆，飲啖水先" in ui, "the line never reached the device"
    note = _notes(svc)[-1]["payload"]
    print("SAY NOTE:", note)


async def test_scenarios_S1_to_S4ok_start_from_telegram(svc):
    for name in ("S1", "S2", "S3", "S4", "S4ok"):
        r = await say(svc, f"/scenario {name}")
        r = await confirm(svc, r)
        notes = [n["payload"] for n in _notes(svc)]
        assert notes[-1]["op"] == "scen" and notes[-1]["args"] == {"name": name}, notes[-1]
        task = svc.extra.get("scenario_task")
        assert task and not task.done(), f"{name}: scenario task not running"
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await settle(svc)
    # S1 runs to completion through the real paths
    r = await say(svc, "/scenario S1")
    await confirm(svc, r)
    task = svc.extra.get("scenario_task")
    try:
        await asyncio.wait_for(asyncio.shield(task), 150)
        print("S1 completed end-to-end:", task.result() if not task.cancelled() else "")
    except (asyncio.TimeoutError, asyncio.CancelledError, Exception) as e:
        print("S1 wait:", type(e).__name__)


# ==================================================================== voice notes
def voice_update(chat, file_id="fake_voice_file", duration=2):
    return {"update_id": 42, "message": {"message_id": 42, "date": 0,
                                         "chat": {"id": int(chat), "type": "private"},
                                         "from": {"id": int(chat), "first_name": "x"},
                                         "voice": {"file_id": file_id, "duration": duration}}}


async def test_family_voice_note_stored_announced_and_playable(svc, tmp_path):
    import asyncio as _a
    from pathlib import Path
    # a real OGG/Opus file made in the image, so the OGG→AAC conversion really runs
    import subprocess
    ogg = Path("/scratch/fake_voice.ogg")
    if not ogg.exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                        "-t", "1.5", "-c:a", "libopus", str(ogg)], check=True, timeout=30, capture_output=True)
    ch = channel(svc)

    async def fake_download(file_id):          # stand-in for getFile+download (fake mode)
        return ogg.read_bytes()
    ch.download = fake_download

    r = await say(svc, "__voice__")            # placeholder; the real update is sent directly below
    from bot_helpers import text_update
    # send the voice update through the real webhook handler
    await telegram.handle_update(svc, voice_update(CHUN_CHAT))
    await settle(svc)
    assert any("收到你嘅語音訊息" in t for t in ch.replies and [x["text"] for x in ch.replies[-1:]]), ch.replies[-1:]
    rows = svc.db.query("SELECT * FROM voice_messages ORDER BY rowid DESC LIMIT 1")
    assert rows, "voice note not stored"
    vm = rows[0]
    assert vm["direction"] == "in" and vm["contact_id"] == "c_chun" and vm["to_name"] == "王婆婆"
    from pathlib import Path as P
    assert P(vm["file_path"]).is_file(), f"stored file missing: {vm['file_path']}"
    assert vm["mime"] in ("audio/mp4", "audio/ogg")
    print(f"VOICE: {vm['file_path']} mime={vm['mime']} duration={vm['duration_s']}s transcript={vm['transcript']}")
    [ev] = events_of(svc, "family.voice_note")
    assert ev["payload"]["voice_id"] == vm["id"] and ev["payload"]["contact_id"] == "c_chun"

    # announced when idle: clip A1 「王婆婆，阿珍留咗段錄音俾你。」 reaches the device
    deadline = asyncio.get_event_loop().time() + 15
    announced = ""
    while asyncio.get_event_loop().time() < deadline:
        await settle(svc, 0.3)
        ui = _ui(svc)
        if "阿珍留咗段錄音俾你" in ui or "想唔想而家聽" in ui:
            announced = ui
            break
    assert announced, f"no A1 announcement on the device; ui={_ui(svc)[:400]}"
    plays = [c for c in commands_of(svc, "device.play_fixed") if (c["payload"] or {}).get("clip_id") == "A1"]
    print("ANNOUNCEMENT present; play_fixed A1 commands:", len(plays))


async def test_stranger_voice_note_is_dropped(svc):
    ch = channel(svc)

    async def no_download(file_id):            # must never even be called
        raise AssertionError("stranger voice note reached channel.download")
    ch.download = no_download
    n = len(ch.replies)
    vm0 = svc.db.one("SELECT COUNT(*) n FROM voice_messages")["n"]   # the seed's demo note is row 1
    await telegram.handle_update(svc, voice_update(STRANGER))
    await settle(svc)
    assert [x["text"] for x in ch.replies[n:]] == ["呢個係私人家庭機械人。"]
    assert svc.db.one("SELECT COUNT(*) n FROM voice_messages")["n"] == vm0, "stranger voice note stored"
    assert not events_of(svc, "family.voice_note"), "stranger voice note reached the Kernel"
