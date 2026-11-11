"""Shared test harness: a real Kernel (fake adapters, in-memory DB, FakeClock) + bridge + text loop + a fake
device that records every command the Device Hub would send and answers like the iPad (acks, playback_done
after the clip's duration, photo uploads)."""
from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

os.environ.setdefault("AHBO_FAKE", "all")
os.environ.setdefault("AHBO_PROFILE", "demo")
os.environ.setdefault("PUBLIC_BASE_URL", "https://test.invalid")

from ahbo.agent.bridge import AgentBridge  # noqa: E402
from ahbo.agent.runner import TurnAgent  # noqa: E402
from ahbo.agent.text_loop import TextLoop  # noqa: E402
from ahbo.core.clock import FakeClock, set_clock  # noqa: E402
from ahbo.core.config import load_config  # noqa: E402
from ahbo.core.db import open_db  # noqa: E402
from ahbo.core.events import Command, Event  # noqa: E402
from ahbo.core.settings import load_settings  # noqa: E402
from ahbo.kernel.core import Kernel  # noqa: E402
from ahbo.kernel.reducers import register_all  # noqa: E402
from ahbo.models.fakes import FakeFlash  # noqa: E402
from ahbo.ops.seed import load_demo  # noqa: E402

TIME_SCALE = float(os.environ.get("AHBO_TEST_TIME_SCALE", "0.05"))


class FakeDevice:
    """Records device commands; replies like the iPad would."""

    def __init__(self, kernel: Kernel, clip_seconds: dict[str, float] | None = None, auto: bool = True):
        self.kernel, self.auto = kernel, auto
        self.sent: list[dict[str, Any]] = []
        self.clip_seconds = clip_seconds or {}
        kernel.on("device", self._on)

    def _on(self, c: Command) -> None:
        self.sent.append({"type": c.type, **c.payload})
        if not self.auto:
            return
        p = c.payload
        if c.type == "device.play_fixed":
            text = self.kernel.config.fixed_lines.get(p["clip_id"], "")
            dur = self.clip_seconds.get(p["clip_id"], max(1.0, len(text) * 0.19))
            self._later(dur, Event(type="device.playback_done", source="device",
                                   payload={"clip_id": p["clip_id"], "say_id": p["say_id"]}))
        elif c.type == "device.play_voice_message":
            self._later(float(p.get("duration_s") or 2.0), Event(type="device.playback_done", source="device",
                        payload={"message_id": p["message_id"], "say_id": p["say_id"]}))
        elif c.type == "device.capture_photo":
            self._later(0.3, Event(type="device.photo_uploaded", source="device",
                                   payload={"photo_id": "ph_test", "incident_id": p["incident_id"]}))

    def _later(self, seconds: float, ev: Event) -> None:
        asyncio.get_running_loop().call_later(seconds * TIME_SCALE, self.kernel.submit_nowait, ev)

    def ui(self) -> list[dict[str, Any]]:
        return [x["ui"] for x in self.sent if x["type"] == "device.ui"]


class Harness:
    def __init__(self, kernel: Kernel, device: FakeDevice, bridge: AgentBridge, loop: TextLoop):
        self.kernel, self.device, self.bridge, self.loop = kernel, device, bridge, loop

    async def say(self, text: str, wait: bool = True) -> dict[str, Any]:
        r = await self.loop.utterance(text)
        if wait and self.loop.task:
            await self.loop.wait_idle()
        return r

    async def event(self, type_: str, source: str = "operator", **payload: Any) -> None:
        await self.kernel.process(Event(type=type_, source=source, payload=payload))

    async def settle(self, seconds: float = 0.5) -> None:
        await asyncio.sleep(seconds)


@pytest.fixture
async def harness(tmp_path):
    clock = FakeClock("2026-11-20T10:00:00+08:00")
    set_clock(clock)
    settings = load_settings()
    db = open_db(":memory:")
    cfg = load_config(fresh=True)
    k = Kernel(db=db, clock=clock, config=cfg, settings=settings, time_scale=TIME_SCALE)
    register_all(k)
    load_demo(db, clock, settings)
    dev = FakeDevice(k)
    bridge = AgentBridge(k)
    loop = TextLoop(bridge, TurnAgent(bridge, FakeFlash()))
    k.start()
    try:
        yield Harness(k, dev, bridge, loop)
    finally:
        await k.stop()
        db.close()


@pytest.fixture
def direct_routes(monkeypatch):
    """Pin the direct provider routes (DeepSeek API, Anthropic API) for tests of that code path. The shipped
    models.yaml routes everything through Opper (owner decision, 11 Nov); the direct routes stay supported."""
    from ahbo.models import runtime
    real = runtime.model_cfg
    direct = {"flash": {"provider": "deepseek", "model": "deepseek-flash",
                        "fallback": {"provider": "anthropic", "model": "claude-haiku-5-5", "effort": "low"}},
              "sonnet": {"provider": "anthropic"}, "vision": {"provider": "anthropic"}}

    def pinned(config, section, purpose=None):
        return {**real(config, section, purpose), **direct.get(section, {})}

    monkeypatch.setattr(runtime, "model_cfg", pinned)
    yield
