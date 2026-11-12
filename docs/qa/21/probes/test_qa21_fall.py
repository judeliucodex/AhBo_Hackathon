"""QA21 check 1: fall incident owner rules (BUILD_LOG §8, PRD §4.3.1)."""
from __future__ import annotations
import asyncio, json, sys
sys.path.insert(0, "/repo/brain/tests")
from conftest import harness  # noqa: F401
from qa import watch, of, faces, clips, ev, req, until, inc, timer, cmds_db, jsends  # noqa: F401
import pytest
from ahbo.core.events import Event
from ahbo.models.fakes import FAKE_VISION_FINE

PROBLEM = "可能有人跌咗喺地下"
NOTES = {"fine": FAKE_VISION_FINE, "problem": PROBLEM, "unclear": "一張模糊嘅相"}


def camera(h, verdict):
    h.kernel.db.kv_set("fake_vision_note", NOTES[verdict])




async def start_fall(h, verdict="fine", source="beacon"):
    camera(h, verdict)
    await ev(h, "sensor.fall_suspected", source, beacon_id="b1", peak_g=3.1)


async def wait_state(h, state, t=5.0):
    ok = await until(lambda: (inc(h) or {}).get("state") == state, t)
    assert ok, f"state never reached {state}, at {(inc(h) or {}).get('state')}"; return ok


async def wait_countdown(h):
    assert await until(lambda: any(x.get("ui", {}).get("type") == "countdown" for x in h.device.sent))


# ---- 1a OK + camera fine → INFO only, no call, no photo in message
async def test_ok_fine_info_only(harness):
    h = harness; watch(h)
    await start_fall(h, "fine")
    await wait_countdown(h)
    await h.say("我冇事", wait=False)
    await wait_state(h, "RESOLVED_OK")
    await asyncio.sleep(0.5)
    assert not cmds_db(h, 'phone.call'), "phone.call must not fire after OK+fine camera"
    ss = cmds_db(h, 'family.send')
    assert ss and all(json.loads(s["payload"])["kind"] == "INFO" for s in ss)
    last = json.loads(ss[-1]["payload"])
    assert "photo_id" not in last, "INFO must not carry the photo (D15)"
    assert last["buttons"] == ["voice_mum"], f"buttons: {last['buttons']}"
    assert "相片睇落冇問題" in last["text"]
    assert inc(h)["state"] == "CLOSED" or await until(lambda: inc(h).get("state") == "CLOSED", 3)


# ---- 1b OK but camera shows a fall → ALERT + call 阿珍 (why=camera)
async def test_ok_but_camera_problem_calls_chun(harness):
    h = harness; watch(h)
    await start_fall(h, "problem")
    await wait_countdown(h)
    await h.say("我冇事", wait=False)
    await wait_state(h, "ESCALATING")
    cl = cmds_db(h, 'phone.call')
    assert cl and all('"c_chun"' in c["payload"] for c in cl)
    assert all("c_ecs" not in c["payload"] for c in cl), "centre must never be called"
    alerts = [json.loads(s["payload"]) for s in cmds_db(h, 'family.send') if json.loads(s["payload"])["kind"] == "ALERT"]
    assert alerts and "相片" in alerts[-1]["text"] or alerts[-1].get("vision_note")
    assert inc(h)["why"] == "camera"


# ---- 1c no answer → ALERT + call 阿珍, ≤3 calls, never centre
async def test_no_answer_escalates_call_cap(harness):
    h = harness; watch(h)
    await start_fall(h, "fine")
    await wait_state(h, "ESCALATING", 8)          # 5 s countdown × 0.05
    assert inc(h)["why"] == "timeout"
    assert len(cmds_db(h, 'phone.call')) == 1
    for _ in range(6):                             # keep missing the ack: tack timer re-arms
        iid = inc(h)["id"]
        await timer(h, f"fall:tack:{iid}", incident_id=iid)
        if inc(h)["calls_placed"] >= 3:
            break
    cl = cmds_db(h, 'phone.call')
    assert len(cl) == 3, f"expected exactly 3 calls, got {len(cl)}"
    assert all('"c_chun"' in c["payload"] and "c_ecs" not in c["payload"] for c in cl)
    assert inc(h)["calls_placed"] == 3


# ---- 1d help words escalate immediately
@pytest.mark.parametrize("word", ["救命", "好痛", "起唔到身", "跌親"])
async def test_help_word_escalates(harness, word):
    h = harness; watch(h)
    await start_fall(h, "fine")
    await wait_countdown(h)
    await h.say(word, wait=False)
    await wait_state(h, "ESCALATING")
    assert inc(h)["why"] == "help"
    assert cmds_db(h, 'phone.call'), "help must call 阿珍"
    assert any(x["kind"] == "ALERT" for x in jsends(h))


# ---- 1e 「冇跌親」 escalates (help wins, owner rule D-16) — behaviour + judgement data
async def test_mou_dei_chun_escalates(harness):
    h = harness; watch(h)
    await start_fall(h, "fine")
    await wait_countdown(h)
    await h.say("我冇跌親", wait=False)
    await wait_state(h, "ESCALATING")
    assert inc(h)["why"] == "help"


# ---- 1f touch button from device resolves; from source=agent must NOT
async def test_touch_ok_sources(harness):
    h = harness; watch(h)
    await start_fall(h, "fine")
    await wait_countdown(h)
    h.kernel.cancel_timer(f"fall:countdown:{inc(h)['id']}")   # freeze the window for the probe
    await asyncio.sleep(0.4)
    await ev(h, "device.touch_ok", "agent")          # forged/agent-sourced
    await asyncio.sleep(0.3)
    assert inc(h)["state"] == "AWAITING_OK", "agent-sourced touch_ok must not resolve"
    assert not cmds_db(h, 'family.send')
    await ev(h, "device.touch_ok", "device")         # real device press
    await wait_state(h, "RESOLVED_OK")
    ss = cmds_db(h, 'family.send')
    assert ss and json.loads(ss[-1]["payload"])["kind"] == "INFO"


# ---- 1g vision note arriving after 「我冇事」 — both outcomes + fine
def slow_vision(h, delay_real_s, box):
    def dispatch(c):
        async def run():
            await asyncio.sleep(delay_real_s)
            iid = c.payload.get("incident_id")
            await h.kernel.submit(Event(type="vision.result", source="system", correlation_id=iid,
                                        payload={"incident_id": iid, "note": box.get("note")}))
        h.kernel.spawn(run(), name="slow_vision")
    h.kernel.on("vision", dispatch)


async def test_late_vision_problem_escalates(harness):
    h = harness; watch(h)
    slow_vision(h, 600.0, {"note": None})            # the real vision job is in flight but not back yet
    await start_fall(h, "fine")
    await wait_countdown(h)
    await h.say("我冇事", wait=False)
    await wait_state(h, "RESOLVED_OK")
    assert not cmds_db(h, 'family.send'), "no INFO while the photo check is pending"
    iid = inc(h)["id"]                               # the note lands inside the ≤6 s decide window
    await ev(h, "vision.result", "system", incident_id=iid, note=PROBLEM)
    await wait_state(h, "ESCALATING", 5)
    assert inc(h)["why"] == "camera"
    assert cmds_db(h, 'phone.call') and any(x["kind"] == "ALERT" for x in jsends(h))


async def test_vision_after_decide_window_does_not_escalate(harness):
    h = harness; watch(h)
    slow_vision(h, 600.0, {"note": None})
    await start_fall(h, "fine")
    await wait_countdown(h)
    await h.say("我冇事", wait=False)
    await wait_state(h, "RESOLVED_OK")
    await asyncio.sleep(0.6)                          # decide timer (6 s × 0.05) fired: INFO stands
    assert any(x["kind"] == "INFO" for x in jsends(h))
    iid = inc(h)["id"]
    await ev(h, "vision.result", "system", incident_id=iid, note=PROBLEM)
    await asyncio.sleep(0.3)
    st = inc(h)["state"]
    assert st in ("RESOLVED_OK", "CLOSED"), f"late problem note must not reopen: {st}"
    assert not cmds_db(h, 'phone.call')


async def test_late_vision_never_arrives_info_only(harness):
    h = harness; watch(h)
    box = {"note": None}
    slow_vision(h, 60.0, box)                        # never lands in time
    await start_fall(h, "fine")
    await wait_countdown(h)
    await h.say("我冇事", wait=False)
    await wait_state(h, "RESOLVED_OK")
    await asyncio.sleep(0.6)                         # decide timer 6 s × 0.05
    ss = cmds_db(h, 'family.send')
    assert ss and json.loads(ss[-1]["payload"])["kind"] == "INFO"
    assert "photo_id" not in json.loads(ss[-1]["payload"])
    assert inc(h)["state"] == "RESOLVED_OK" or inc(h)["state"] == "CLOSED"


async def test_late_vision_fine_info_only(harness):
    h = harness; watch(h)
    box = {"note": FAKE_VISION_FINE}
    slow_vision(h, 0.12, box)
    await start_fall(h, "fine")
    await wait_countdown(h)
    await h.say("我冇事", wait=False)
    await wait_state(h, "RESOLVED_OK")
    await wait_state(h, "CLOSED", 6)
    ss = [json.loads(s["payload"]) for s in cmds_db(h, 'family.send')]
    assert ss and all(s["kind"] == "INFO" for s in ss)
    assert not cmds_db(h, 'phone.call')


# ---- 1h repeat fall during an open incident attaches
async def test_repeat_fall_attaches(harness):
    h = harness; watch(h)
    await start_fall(h, "fine")
    await wait_countdown(h)
    n = h.kernel.db.one("SELECT COUNT(*) AS n FROM incidents")["n"]
    await ev(h, "sensor.fall_suspected", "beacon", beacon_id="b1")
    await asyncio.sleep(0.2)
    tl = inc(h)["timeline"]
    assert any(x.get("what") == "attached" for x in tl)
    assert h.kernel.db.one("SELECT COUNT(*) AS n FROM incidents")["n"] == n


# ---- 1i 60 s cooldown after close
async def test_cooldown_60s(harness):
    h = harness; watch(h)
    await start_fall(h, "fine")
    await wait_countdown(h)
    await ev(h, "device.touch_ok", "device")
    await wait_state(h, "RESOLVED_OK")
    await wait_state(h, "CLOSED", 6)
    n = h.kernel.db.one("SELECT COUNT(*) AS n FROM incidents")["n"]
    await ev(h, "sensor.fall_suspected", "beacon", beacon_id="b1")     # 5 s later
    await asyncio.sleep(0.2)
    tl = inc(h)["timeline"]
    assert any(x.get("what") == "cooldown" for x in tl), "must stay in cooldown"
    assert h.kernel.db.one("SELECT COUNT(*) AS n FROM incidents")["n"] == n
    h.kernel.clock.advance(61)
    await ev(h, "sensor.fall_suspected", "beacon", beacon_id="b1")
    await wait_countdown(h)
    assert h.kernel.db.one("SELECT COUNT(*) AS n FROM incidents")["n"] == n + 1


# ---- family buttons: handled acknowledges; resolved closes
async def test_family_buttons_ack_and_resolve(harness):
    h = harness; watch(h)
    await start_fall(h, "fine")
    await wait_state(h, "ESCALATING", 8)
    iid = inc(h)["id"]
    await ev(h, "family.button", "telegram", incident_id=iid, contact_id="c_chun", button="handled")
    await wait_state(h, "ACKNOWLEDGED")
    acks = [x for x in jsends(h) if x["kind"] == "NOTIFY" and "跟進緊" in x["text"]]
    assert acks, "acknowledgement notice to the family"
    await ev(h, "family.button", "telegram", incident_id=iid, contact_id="c_chun", button="resolved")
    await wait_state(h, "CLOSED")
    assert h.kernel.state.session is None or inc(h)["state"] == "CLOSED"
