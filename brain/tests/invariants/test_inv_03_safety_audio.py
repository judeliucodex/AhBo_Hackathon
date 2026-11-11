"""INV-3: safety audio never depends on an LLM, STT, TTS or LiveKit. The fall flow completes with Flash, vision and
the session launcher all raising, and with a silent (disconnected) iPad; fixed clips are referenced by id only."""
from __future__ import annotations

from inv_helpers import fall, need, played, until_state

SAFETY_CLIPS = ("F1", "F2", "F3", "F4", "F4p", "F5", "G1", "G2")


async def test_safety_lines_are_fixed_clips(system):
    for clip in SAFETY_CLIPS:
        assert system.config.fixed_lines.get(clip), f"fixed line {clip} missing from config/fixed_lines.yaml"


async def test_fall_completes_with_models_and_livekit_down(make_system):
    s = await make_system(break_adapters=["flash", "vision", "session"])
    need(s, "reducer:fall")
    await fall(s)
    assert await until_state(s, "ESCALATING", 60), f"fall stalled with adapters down: {s.incident_path}"
    await s.wait(8)
    clips = played(s)
    for c in ("F1", "F2", "F3"):
        assert c in clips, f"{c} not played: {clips}"
    assert "F4" in clips or "F4p" in clips, clips
    assert [c for c in s.commands("phone.call") if c["payload"].get("contact_id") == "c_chun"], "no call to the caregiver"
    assert [c for c in s.commands("family.send") if c["payload"].get("kind") == "ALERT"], "no ALERT to the family"
    for c in s.commands("device.play_fixed"):
        assert c["payload"].get("clip_id"), "safety audio must be a cached clip id, never synthesised text"


async def test_fall_escalates_with_a_silent_ipad(make_system):
    """No playback_done, no photo: the Kernel's own max-duration timers carry the flow (§10.4)."""
    s = await make_system(device_auto=False)
    need(s, "reducer:fall")
    await fall(s)
    assert await until_state(s, "ESCALATING", 80), f"fall stalled without the iPad: {s.incident_path}"
    assert s.commands("phone.call") and s.commands("family.send")


async def test_llm_failure_gets_the_fixed_fallback(make_system):
    """With Flash down a conversation turn still answers (G1-style fallback), and nothing crashes."""
    s = await make_system(break_adapters=["flash"])
    await s.say("阿寶，我幾時食藥呀？")
    await s.settle()
    assert any("諗唔到嘢" in ln for ln in s.lines()), s.lines()
