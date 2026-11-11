"""§23.8 golden UI traces: the Kernel in fake mode must send the kit storyboards' commands in kind and order."""
from __future__ import annotations

import asyncio

import pytest

from golden import compare, from_device, kit_trace


async def _run_s(h, anchor: str, lines: list[str]):
    h.kernel.clock.set_time(anchor)
    for i, line in enumerate(lines):
        await h.say(line)
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.4)   # step-group clear timer (4 s × time scale)
    return from_device(h.device.sent)


@pytest.mark.parametrize("name,anchor,lines", [
    ("S1", "09:00", ["阿寶，今日有冇人搵過我呀？"]),
    ("S2", "10:00", ["阿寶，我幾時食藥呀？"]),
    ("S3", "08:30", ["阿寶，我今日有咩安排呀？", "咁我下晝好悶喎……"]),
])
async def test_conversation_storyboards(harness, name, anchor, lines):
    ours = await _run_s(harness, anchor, lines)
    ok, report = compare(kit_trace(name), ours)
    assert ok, report


@pytest.mark.parametrize("name", ["S4", "S4ok"])
async def test_fall_storyboards(harness, name):
    h = harness
    h.kernel.clock.set_time("15:00")
    from ahbo.models.fakes import FAKE_VISION_FINE, FAKE_VISION_KEY
    h.kernel.db.kv_set(FAKE_VISION_KEY, FAKE_VISION_FINE if name == "S4ok" else "")
    await h.event("sensor.fall_suspected", source="beacon", beacon_id="beacon1", peak_g=3.1)
    if name == "S4ok":
        for _ in range(200):
            await asyncio.sleep(0.02)
            if any(x.get("ui", {}).get("type") == "countdown" for x in h.device.sent):
                break
        await asyncio.sleep(0.05)
        await h.say("我冇事", wait=False)
    await asyncio.sleep(3.0)
    ok, report = compare(kit_trace(name), from_device(h.device.sent))
    assert ok, report
