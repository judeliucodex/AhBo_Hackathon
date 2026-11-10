"""INV-5: one voice at a time. Her new words cut Ah Bo's reply (barge-in); a fall preempts a reply (agent paused,
caption ended) and the agent says nothing over the safety clips; social items wait for the arbiter."""
from __future__ import annotations

from inv_helpers import fall, need, until_state


def _ui(s):
    return s.device.ui()


async def test_barge_in_ends_the_current_reply_first(system):
    s = system
    s.clock.set_time("10:00")
    await s.say("阿寶，我幾時食藥呀？", wait=False)
    assert await s.wait_for(lambda: any(u.get("type") == "say_start" for u in _ui(s)), 20)
    first = next(u for u in _ui(s) if u.get("type") == "say_start")
    await s.say("而家幾點呀？")
    seq = [(u.get("type"), u.get("id")) for u in _ui(s) if u.get("type") in ("say_start", "say_end")]
    i_end = seq.index(("say_end", first["id"]))
    later_starts = [i for i, x in enumerate(seq) if x[0] == "say_start" and x[1] != first["id"]]
    assert later_starts and i_end < later_starts[0], f"two voices overlapped: {seq}"


async def test_fall_preempts_a_reply_and_the_agent_stays_silent(system):
    s = system
    need(s, "reducer:fall")
    s.clock.set_time("10:00")
    await s.say("阿寶，我幾時食藥呀？", wait=False)
    assert await s.wait_for(lambda: any(u.get("type") == "say_start" for u in _ui(s)), 20)
    open_say = next(u for u in _ui(s) if u.get("type") == "say_start")["id"]
    n_before = len(s.device.sent)
    await fall(s)
    assert await until_state(s, "ESCALATING", 60)
    after = s.device.sent[n_before:]
    f1 = next(i for i, x in enumerate(after) if x["type"] == "device.play_fixed" and x.get("clip_id") == "F1")
    assert s.commands("agent.pause"), "the agent must be paused (hard) at ALARM"
    pause = [c for c in s.commands("agent.pause")][0]
    assert pause["priority"] == 0
    ended = any(x["type"] == "device.ui" and x["ui"].get("type") == "say_end" and x["ui"].get("id") == open_say
                for x in after[:f1 + 1]) or any(x["type"] == "device.stop_all" for x in after[:f1 + 1]) or \
        any(x["type"] == "device.ui" and x["ui"].get("type") == "reset" for x in after[:f1 + 1])
    assert ended, "Ah Bo's caption/voice must stop before F1"
    # between ALARM and ESCALATING no agent caption is started
    agent_says = [x for x in after[:f1 + 200] if x["type"] == "device.ui" and x["ui"].get("type") == "say_start"
                  and x["ui"].get("from") != "family" and x["ui"].get("id") != open_say]
    assert not agent_says, f"the agent spoke over the safety flow: {agent_says}"


async def test_voice_note_waits_for_an_idle_gap(system):
    s = system
    need(s, "reducer:arbiter")
    s.clock.set_time("10:00")
    await s.say("阿寶，我幾時食藥呀？", wait=False)
    await s.event("family.voice_note", "telegram", contact_id="c_chun", voice_id="vm_demo")
    decisions = [e["payload"] for e in s.events("arbiter.decision")]
    assert decisions and decisions[0].get("outcome") in ("queued", "dropped"), decisions
