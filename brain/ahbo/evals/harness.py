"""The whole system in-process, in fake mode (§16.1, §26.3): the real Kernel with every reducer, every component's
`wiring.install(svc)` (links, memory, deep, actions, models, …), an in-memory DB seeded with `seed/demo.yaml`, a
FakeClock, a recording device that answers like the iPad, and the text loop with the configured Flash.

Same pattern as `brain/tests/conftest.py` (harness fixture), plus the component wiring so commands such as
`family.send` and `phone.call` reach their real dispatchers (which write the fake outbox). Used by the eval runner
and by `tests/invariants/`.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
import pkgutil
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("AHBO_FAKE", "all")
os.environ.setdefault("AHBO_PROFILE", "demo")

from ahbo.agent.bridge import AgentBridge  # noqa: E402
from ahbo.agent.runner import TurnAgent  # noqa: E402
from ahbo.agent.text_loop import TextLoop  # noqa: E402
from ahbo.agent.tools import TOOLS, load_tools  # noqa: E402
from ahbo.core.adapters import FlashLLM  # noqa: E402
from ahbo.core.clock import FakeClock, set_clock  # noqa: E402
from ahbo.core.config import load_config  # noqa: E402
from ahbo.core.db import open_db  # noqa: E402
from ahbo.core.events import Command, Event  # noqa: E402
from ahbo.core.jsonutil import loads  # noqa: E402
from ahbo.core.settings import load_settings  # noqa: E402
from ahbo.kernel.core import Kernel  # noqa: E402
from ahbo.kernel.reducers import register_all  # noqa: E402
from ahbo.ops.seed import load_demo  # noqa: E402

log = logging.getLogger("ahbo.evals")

DEFAULT_START = "2026-11-20T10:00:00+08:00"
SKIP_WIRING = {"api", "evals", "core", "ops", "trace"}   # the platform (device hub, HTTP) is replaced by the recorder


def time_scale() -> float:
    return float(os.environ.get("AHBO_TEST_TIME_SCALE", "0.05"))


class RecordingDevice:
    """Records every command the Device Hub would send and replies like the iPad: playback_done after the clip's
    duration, a photo upload after capture_photo. `auto=False` is a silent (disconnected) iPad."""

    def __init__(self, kernel: Kernel, scale: float, auto: bool = True):
        self.kernel, self.scale, self.auto = kernel, scale, auto
        self.sent: list[dict[str, Any]] = []

    def __call__(self, c: Command) -> None:
        self.sent.append({"type": c.type, "priority": c.priority, **c.payload})
        if not self.auto:
            return
        p = c.payload
        if c.type == "device.play_fixed":
            text = self.kernel.config.fixed_lines.get(p.get("clip_id", ""), "") or p.get("text") or ""
            self._later(max(1.0, len(text) * 0.19), Event(type="device.playback_done", source="device",
                        payload={"clip_id": p.get("clip_id"), "say_id": p.get("say_id")}))
        elif c.type == "device.play_voice_message":
            self._later(float(p.get("duration_s") or 2.0), Event(type="device.playback_done", source="device",
                        payload={"message_id": p.get("message_id"), "say_id": p.get("say_id")}))
        elif c.type == "device.capture_photo":
            self._later(0.3, Event(type="device.photo_uploaded", source="device",
                                   payload={"photo_id": "ph_eval", "incident_id": p.get("incident_id")}))

    def _later(self, seconds: float, ev: Event) -> None:
        asyncio.get_running_loop().call_later(seconds * self.scale, self.kernel.submit_nowait, ev)

    def ui(self) -> list[dict[str, Any]]:
        return [x["ui"] for x in self.sent if x["type"] == "device.ui"]


def _raising(name: str) -> Callable[[Command], None]:
    def fn(c: Command) -> None:
        raise RuntimeError(f"{name} adapter down (eval drill)")
    return fn


class EvalSystem:
    def __init__(self) -> None:
        self.kernel: Kernel
        self.loaded_reducers: list[str] = []
        self.wired: list[str] = []
        self.wiring_errors: dict[str, str] = {}
        self.incident_path: list[str] = []
        self.observed: list[tuple[str, dict[str, Any]]] = []

    @classmethod
    async def create(cls, *, flash: FlashLLM | None = None, start: str = DEFAULT_START, wiring: bool = True,
                     device_auto: bool = True, break_adapters: list[str] | tuple[str, ...] = (),
                     scale: float | None = None) -> EvalSystem:
        self = cls()
        self.scale = time_scale() if scale is None else scale
        self.clock = FakeClock(start)
        set_clock(self.clock)
        self.settings = load_settings()
        self.db = open_db(":memory:")
        self.config = load_config(fresh=True)
        k = self.kernel = Kernel(db=self.db, clock=self.clock, config=self.config, settings=self.settings,
                                 time_scale=self.scale)
        self.loaded_reducers = register_all(k)
        load_demo(self.db, self.clock, self.settings)
        load_tools()
        if "flash" in break_adapters:
            from .scripted import RaisingFlash
            flash = RaisingFlash()
        if flash is None:
            from ahbo.models import get_flash
            flash = get_flash()
        self.flash = flash
        self.bridge = AgentBridge(k)
        self.agent = TurnAgent(self.bridge, flash)
        self.loop = TextLoop(self.bridge, self.agent)
        self.device = RecordingDevice(k, self.scale, auto=device_auto)
        self.svc = SimpleNamespace(kernel=k, db=self.db, clock=self.clock, config=self.config,
                                   settings=self.settings, bridge=self.bridge, text_loop=self.loop, hub=self.device,
                                   device=self.device, flash=flash, spawn=k.spawn)
        if wiring:
            self._install_wiring()
        self._install_device()
        for name in break_adapters:
            if name != "flash":
                self.break_adapter(name)
        k.subscribe(self._observe)
        k.load()
        k.start()
        return self

    # ---------------------------------------------------------------- wiring
    def _install_wiring(self) -> None:
        import ahbo
        for m in sorted(pkgutil.iter_modules(ahbo.__path__), key=lambda m: m.name):
            if not m.ispkg or m.name in SKIP_WIRING:
                continue
            try:
                mod = importlib.import_module(f"ahbo.{m.name}.wiring")
            except ModuleNotFoundError as e:
                if e.name in (f"ahbo.{m.name}.wiring",):
                    continue
                self.wiring_errors[m.name] = f"import: {e!r}"
                continue
            except Exception as e:  # a broken component must not stop the evals of the others
                self.wiring_errors[m.name] = f"import: {e!r}"
                continue
            if not hasattr(mod, "install"):
                continue
            try:
                mod.install(self.svc)
                self.wired.append(m.name)
            except Exception as e:
                log.exception("wiring %s failed", m.name)
                self.wiring_errors[m.name] = f"install: {e!r}"

    def _install_device(self) -> None:
        """The recorder sees every device command; component dispatchers for `device.*` types still run after it."""
        k = self.kernel
        for key, fn in list(k.dispatchers.items()):
            if key.startswith("device.") and fn is not self.device:
                def chained(c: Command, _fn=fn):
                    self.device(c)
                    return _fn(c)
                k.dispatchers[key] = chained
        k.on("device", self.device)

    def break_adapter(self, name: str) -> None:
        """Make an adapter's dispatcher raise: `vision`, `session` (LiveKit), `telegram`, `twilio`, `deep`."""
        keys = {"vision": ["vision.analyze", "vision"], "session": ["session.open", "session"],
                "telegram": ["family.send", "family"], "twilio": ["phone.call", "phone"],
                "deep": ["deep.start", "deep"]}[name]
        for key in keys:
            self.kernel.on(key, _raising(name))

    # ---------------------------------------------------------------- observation
    def _observe(self, kind: str, obj: dict[str, Any]) -> None:
        self.observed.append((kind, obj))
        st = self.incident_state()
        if st and (not self.incident_path or self.incident_path[-1] != st):
            self.incident_path.append(st)

    def incident_state(self) -> str | None:
        inc = self.kernel.state.incident
        if inc is None:
            return None
        return inc.get("state") if isinstance(inc, dict) else getattr(inc, "state", None)

    def events(self, type_: str | None = None) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT rowid AS seq, * FROM events ORDER BY rowid") if type_ is None else \
            self.db.query("SELECT rowid AS seq, * FROM events WHERE type=? ORDER BY rowid", (type_,))
        for r in rows:
            r["payload"] = loads(r["payload"], {})
        return rows

    def commands(self, type_: str | None = None) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT rowid AS seq, * FROM commands ORDER BY rowid") if type_ is None else \
            self.db.query("SELECT rowid AS seq, * FROM commands WHERE type=? ORDER BY rowid", (type_,))
        for r in rows:
            r["payload"] = loads(r["payload"], {})
        return rows

    def outbox(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM outbox ORDER BY rowid")
        for r in rows:
            r["payload"] = loads(r["payload"], {})
        return rows

    def tool_calls(self) -> list[dict[str, Any]]:
        return [e["payload"] for e in self.events("agent.tool_call")]

    def tool_results(self) -> list[dict[str, Any]]:
        return [e["payload"] for e in self.events("agent.tool_result")]

    def spoken(self) -> list[str]:
        """Every line Ah Bo speaks: text-session captions (`say_start` not from family) and fixed clips."""
        out: list[str] = []
        for x in self.device.sent:
            if x["type"] == "device.ui":
                u = x.get("ui") or {}
                if u.get("type") == "say_start" and u.get("from") != "family" and u.get("text"):
                    out.append(u["text"])
            elif x["type"] == "device.play_fixed":
                t = self.config.fixed_lines.get(x.get("clip_id", "")) or x.get("text")
                if t:
                    out.append(t)
        return out

    def replies(self) -> list[str]:
        """What the agent replied (after the spoken filter), as recorded by the Kernel."""
        return [e["payload"].get("text", "") for e in self.events("agent.reply_start") if e["payload"].get("text")]

    def lines(self) -> list[str]:
        seen, out = set(), []
        for t in self.spoken() + self.replies():
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out

    # ---------------------------------------------------------------- driving
    async def say(self, text: str, *, wait: bool = True, stt_confidence: float = 1.0) -> dict[str, Any]:
        r = await self.loop.utterance(text, stt_confidence=stt_confidence)
        if wait and self.loop.task:
            try:
                await self.loop.wait_idle(timeout=60)
            except asyncio.TimeoutError:
                r = {**r, "timeout": True}
        return r

    async def event(self, type_: str, source: str = "operator", *, correlation_id: str | None = None,
                    request: bool = False, **payload: Any) -> dict[str, Any] | None:
        ev = Event(type=type_, source=source, correlation_id=correlation_id, payload=payload)
        if request:
            return await self.kernel.request(ev)
        await self.kernel.process(ev)
        return None

    async def wait(self, scenario_seconds: float) -> None:
        await asyncio.sleep(scenario_seconds * self.scale)

    async def wait_for(self, pred: Callable[[], bool], scenario_timeout: float = 30.0) -> bool:
        deadline = asyncio.get_running_loop().time() + scenario_timeout * self.scale + 0.5
        while asyncio.get_running_loop().time() < deadline:
            if pred():
                return True
            await asyncio.sleep(0.01)
        return pred()

    async def settle(self) -> None:
        for _ in range(200):
            busy = not self.kernel.queue.empty() or (self.loop.task is not None and not self.loop.task.done())
            if not busy:
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.05)

    async def close(self) -> None:
        if self.loop.task and not self.loop.task.done():
            self.loop.task.cancel()
        await self.kernel.stop()
        self.db.close()

    # ---------------------------------------------------------------- what exists
    def has(self, need: str) -> bool:
        """Feature checks used by `needs:` in suites and by the invariant tests' skips."""
        kind, _, name = need.partition(":")
        if kind == "reducer":
            return name in self.loaded_reducers
        if kind == "tool":
            return name in TOOLS
        if kind == "wiring":
            return name in self.wired
        if kind == "dispatch":
            return name in self.kernel.dispatchers or name.split(".", 1)[0] in self.kernel.dispatchers
        if kind == "models":
            return (name == "real") == (not self.settings.is_fake("models"))
        if kind == "module":
            try:
                importlib.import_module(name)
                return True
            except Exception:
                return False
        raise ValueError(f"unknown need {need!r}")
