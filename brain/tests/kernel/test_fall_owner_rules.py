"""Owner decision 11 Nov: after a fall, 「我冇事」 + a camera that shows no problem → Telegram notification only;
a real problem (no answer, a help word, or the camera showing a fall even after 「我冇事」) → alert + call 阿珍.
Calls go to the caregiver only. The camera can only add a call, never remove one (INV-2)."""
from __future__ import annotations

import asyncio

from ahbo.kernel.reducers.fall import vision_verdict
from ahbo.models.fakes import FAKE_VISION_FINE, FAKE_VISION_KEY


def _sent(h, type_):
    return [x for x in h.device.sent if x["type"] == type_]


async def _commands(h, type_):
    return h.kernel.db.query("SELECT * FROM commands WHERE type=? ORDER BY ts", (type_,))


async def _fall(h, note: str):
    h.kernel.db.kv_set(FAKE_VISION_KEY, note)
    await h.event("sensor.fall_suspected", source="beacon", beacon_id="beacon1")


async def _until(pred, t=5.0):
    for _ in range(int(t / 0.02)):
        if pred():
            return True
        await asyncio.sleep(0.02)
    return False


def _countdown_shown(h):
    return any(x.get("ui", {}).get("type") == "countdown" for x in h.device.sent)


def test_vision_verdict_template():
    assert vision_verdict("可能有人跌咗喺地下") == "problem"
    assert vision_verdict(FAKE_VISION_FINE) == "fine"
    assert vision_verdict("一張模糊嘅相") == "unclear"
    assert vision_verdict(None) == "unclear"


async def test_ok_and_camera_fine_notifies_only(harness):
    h = harness
    await _fall(h, FAKE_VISION_FINE)
    assert await _until(lambda: _countdown_shown(h))
    await h.say("我冇事", wait=False)
    await asyncio.sleep(1.0)
    assert not await _commands(h, "phone.call")
    sends = await _commands(h, "family.send")
    assert sends and all('"INFO"' in s["payload"] for s in sends)
    assert "相片睇落冇問題" in sends[-1]["payload"]


async def test_ok_but_camera_shows_fall_calls_caregiver(harness):
    h = harness
    await _fall(h, "可能有人跌咗喺地下")
    assert await _until(lambda: _countdown_shown(h))
    await h.say("我冇事", wait=False)
    await asyncio.sleep(1.0)
    calls = await _commands(h, "phone.call")
    assert calls and all('"c_chun"' in c["payload"] for c in calls)          # 阿珍, never the centre
    assert any('"ALERT"' in s["payload"] for s in await _commands(h, "family.send"))


async def test_no_answer_calls_caregiver_not_centre(harness):
    h = harness
    await _fall(h, FAKE_VISION_FINE)
    assert await _until(lambda: _countdown_shown(h))
    await asyncio.sleep(1.0)                    # 5 s countdown × time scale
    calls = await _commands(h, "phone.call")
    assert calls and all('"c_chun"' in c["payload"] and "c_ecs" not in c["payload"] for c in calls)
    texts = [x["ui"]["card"]["label"] for x in _sent(h, "device.ui") if x["ui"].get("type") == "card"]
    assert "致電阿珍" in texts and "緊急中心" not in texts


async def test_help_word_calls_caregiver(harness):
    h = harness
    await _fall(h, FAKE_VISION_FINE)
    assert await _until(lambda: _countdown_shown(h))
    await h.say("救命呀", wait=False)
    await asyncio.sleep(0.5)
    assert await _commands(h, "phone.call")
