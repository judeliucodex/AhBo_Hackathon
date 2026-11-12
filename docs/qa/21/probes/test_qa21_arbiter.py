"""QA21 check 5: arbiter (§6) — P0 preempts, P2 idle gap, quiet hours, one pending per class, decisions traced."""
from __future__ import annotations
import asyncio, sys
sys.path.insert(0, "/repo/brain/tests")
import pytest
from conftest import harness  # noqa: F401
from qa import watch, of, faces, clips, until, ev, req, inc
from ahbo.core.jsonutil import loads
from ahbo.kernel import arbiter

def decisions(h):
    return [loads(r["payload"]) for r in h.kernel.db.query(
        "SELECT payload FROM events WHERE type='arbiter.decision'")]

async def busy_session(h):
    """Open a session and pin it busy (agent speaking) so every request must queue."""
    await ev(h, "device.wake", "device", trigger="touch")
    assert h.kernel.state.session is not None
    h.kernel.state.session.agent_state = "speaking"


async def test_p0_preempts_a_reply(harness):
    h = harness; watch(h)
    await h.say("阿寶，今日天氣點呀？", wait=False)          # turn in flight
    await ev(h, "sensor.fall_suspected", "beacon", beacon_id="b1")
    assert await until(lambda: "F1" in clips(h), 3)
    assert of(h, "agent.pause", hard=True), "P0 must hard-pause the agent"
    assert any(u.get("mood") == "alarm" for u in faces(h, "mood"))
    starts = [u for u in faces(h, "say_start")]
    ends = [u for u in faces(h, "say_end")]
    if starts:                                             # any in-flight caption must be closed by the preemption
        assert len(ends) >= len(starts), "P0 must stop the caption in flight (INV-5)"


async def test_p2_voice_note_waits_for_idle_gap(harness):
    h = harness; watch(h)
    await h.say("阿寶，我幾時食藥呀？", wait=False)
    await ev(h, "family.voice_note", "telegram", contact_id="c_chun", voice_id="vm_demo")
    ds = [d for d in decisions(h) if d["request"].get("kind") == "錄音"]
    assert ds and ds[0]["outcome"] == "queued" and ds[0]["reason"] == "idle_gap"
    assert not clips(h), "must not talk over the reply"
    await h.loop.wait_idle()
    assert await until(lambda: "A1" in clips(h), 5), "delivered in the idle gap after the reply"
    got = [d for d in decisions(h) if d["request"].get("kind") == "錄音" and d["outcome"] == "now"]
    assert got and got[-1]["reason"].startswith("dequeued")


async def test_quiet_hours_rules(harness):
    h = harness; watch(h)
    h.kernel.clock.set_time("23:30")
    await ev(h, "family.voice_note", "telegram", contact_id="c_chun", voice_id="vm_demo")   # P2
    await ev(h, "family.text", "telegram", contact_id="c_chun", text="媽媽聽日去飲茶呀")      # P2
    assert any(d["outcome"] == "queued" and d["reason"] == "quiet_hours_next_session"
               for d in decisions(h)), "P2 queues for the next session"
    # one pending item per class: the second P2 replaced the first
    repl = [d for d in decisions(h) if d["outcome"] == "dropped" and d["reason"] == "replaced"]
    assert repl, "second P2 must replace the first"
    await ev(h, "checkin.due", "scheduler", kind="evening")                                  # P3
    skips = [r["payload"] for r in h.kernel.db.query("SELECT payload FROM events WHERE type='checkin.skipped'")]
    assert skips and '"quiet_hours"' in skips[-1], f"check-in must be skipped in quiet hours: {skips}"
    print("QH-DECISIONS:", [(d["request"]["kind"], d["priority"], d["outcome"], d["reason"]) for d in decisions(h)])
    # P1 non-dose (leave_soon) dropped
    await ev(h, "event.leave_soon", "scheduler", event_id="cal_x")
    print("QH-DECISIONS:", [(d["request"]["kind"], d["priority"], d["outcome"], d["reason"]) for d in decisions(h)])
    assert any(d["priority"] == 1 and d["outcome"] == "dropped" and d["reason"] == "quiet_hours"
               for d in decisions(h)), "P1 non-dose must drop in quiet hours"
    assert not clips(h), "nothing delivered in quiet hours so far"
    # P1 dose is allowed
    await ev(h, "dose.due", "scheduler", slot_id="s_1200", date=h.kernel.clock.today().isoformat())
    assert await until(lambda: "R_s_1200" in clips(h), 4), "P1 dose reminder allowed in quiet hours"


async def test_queued_p2_delivered_at_next_session(harness):
    h = harness; watch(h)
    h.kernel.clock.set_time("23:30")
    await ev(h, "family.text", "telegram", contact_id="c_chun", text="媽媽聽日去飲茶呀")
    await asyncio.sleep(0.2)
    assert not of(h, "agent.inject")
    h.kernel.clock.set_time("09:00")
    await ev(h, "device.wake", "device", trigger="touch")                    # next session
    assert await until(lambda: of(h, "agent.inject", kind="家人"), 5), "queued P2 speaks at the next session"


async def test_p1_items_merge(harness):
    h = harness; watch(h)
    await busy_session(h)
    await ev(h, "dose.due", "scheduler", slot_id="s_1200", date=h.kernel.clock.today().isoformat())
    await ev(h, "dose.due", "scheduler", slot_id="s_2000", date=h.kernel.clock.today().isoformat())
    q = arbiter.slice_(h.kernel.state)["queue"]
    assert "1" in q, "P1 item pending"
    merged = q["1"]
    assert "十二點正" in merged["instruction"] and "八點正" in merged["instruction"], \
        f"both doses merged into one item: {merged['instruction']}"
    assert len(merged["cards"]) == 7 and {c["label"] for c in merged["cards"]} >= {"12:00 食藥", "8:00 食藥"}, \
        f"cards from both slots kept: {len(merged['cards'])}"
    h.kernel.state.session.agent_state = "idle"
    h.kernel.state.session = None
    await ev(h, "schedule.tick", "scheduler")                                 # pump on tick
    assert await until(lambda: "R_s_1200" in clips(h), 5)


# --- DEFECT EVIDENCE (report D3): a queued P1 non-dose IS delivered by its max-wait timer during quiet
# hours — PRD §6 says only P0 and P1 doses may speak. The direct path drops it (see test_quiet_hours_rules);
# the `arb:p1` timer path checks only incident/mute. Assertion below documents the buggy behaviour on purpose.
async def test_p1_maxwait_timer_vs_quiet_hours(harness):
    h = harness; watch(h)
    await busy_session(h)                                                     # busy → P1 queues
    item = arbiter.make_item("家人", 1, "阿珍有嘢話你知", "轉述家人訊息。", after_turn=True)
    outcome, cmds = arbiter.request(h.kernel.state, h.kernel.ctx(), item)
    assert outcome == "queued"
    await h.kernel.emit(*cmds)                                                # the timer.set must actually be armed
    h.kernel.clock.set_time("23:30")                                          # quiet hours start
    q = arbiter.slice_(h.kernel.state)["queue"]
    assert "1" in q
    await asyncio.sleep(2.0)                                                  # arb:p1 max-wait (30 s × 0.05)
    inj = of(h, "agent.inject")
    ds = decisions(h)
    print("QUIET-HOLE inject delivered:", bool(inj), "| decisions:",
          [(d["request"]["kind"], d["outcome"], d["reason"]) for d in ds if d["request"]["kind"] == "家人"])
    assert inj != [], "BUG DOCUMENTED: quiet hours did NOT stop the queued non-dose P1 (max_wait path)"


async def test_incident_blocks_everything_but_p0(harness):
    h = harness; watch(h)
    await ev(h, "sensor.fall_suspected", "beacon", beacon_id="b1")
    assert await until(lambda: any(u.get("ui", {}).get("type") == "countdown" for u in h.device.sent))
    h.kernel.cancel_timer(f"fall:countdown:{inc(h)['id']}")
    await ev(h, "family.voice_note", "telegram", contact_id="c_chun", voice_id="vm_demo")     # P2
    await ev(h, "family.text", "telegram", contact_id="c_chun", text="媽媽食咗飯未呀")          # P2
    await ev(h, "checkin.due", "scheduler", kind="morning")                                    # P3
    ds = [d for d in decisions(h)]
    assert all(d["outcome"] != "now" for d in ds), f"nothing non-P0 may speak during an incident: {ds}"
    assert not of(h, "agent.inject")
