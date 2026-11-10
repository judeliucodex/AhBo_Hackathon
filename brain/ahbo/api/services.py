"""The services container (`svc`, AGENT_BRIEF "wiring convention").

`build_services()` opens the DB, builds the Kernel, registers reducers, builds the agent bridge + text loop and the
device hub, then calls every component's `ahbo.<pkg>.wiring.install(svc)` that exists. Components that are not
built yet are skipped, so the api always comes up.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ahbo.core.clock import Clock, get_clock, set_clock
from ahbo.core.config import Config, load_config, load_seed
from ahbo.core.db import Database, open_db
from ahbo.core.settings import Settings, get_settings
from ahbo.kernel.core import Kernel

log = logging.getLogger("ahbo.api")

# Component packages whose `wiring.install(svc)` is called (kernel first: it registers the reducers).
COMPONENTS = ("kernel", "agent", "memory", "links", "actions", "deep", "models", "speech", "trace", "wake")
CLOCK_KV = "clock_offset_s"


def _import_optional(name: str) -> Any | None:
    """Import a module that may not exist yet; a missing module is None, a broken one is logged."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as e:
        if e.name and (name == e.name or name.startswith(e.name + ".")):
            return None
        log.exception("import of %s failed (missing dependency %s)", name, e.name)
        return None
    except Exception:
        log.exception("import of %s failed", name)
        return None


@dataclass
class Services:
    settings: Settings
    db: Database
    clock: Clock
    config: Config
    kernel: Kernel
    bridge: Any = None
    text_loop: Any = None
    agent: Any = None
    hub: Any = None
    routers: list[Any] = field(default_factory=list)
    installed: list[str] = field(default_factory=list)
    reducers: list[str] = field(default_factory=list)
    on_reset: list[Callable[[Services], Any]] = field(default_factory=list)
    on_start: list[Callable[[Services], Any]] = field(default_factory=list)
    on_stop: list[Callable[[Services], Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    _tasks: set[asyncio.Task] = field(default_factory=set)

    def spawn(self, coro: Awaitable[Any], name: str = "bg") -> asyncio.Task:
        return self.kernel.spawn(coro, name)

    def publish(self, kind: str, obj: dict[str, Any]) -> None:
        """Push something to the operator stream (SSE) without a Kernel event."""
        self.kernel._notify(kind, obj)

    def seed(self) -> dict[str, Any]:
        return load_seed("demo.yaml", self.settings.public_base_url)

    # ---------- clock ----------
    def save_clock(self) -> None:
        self.db.kv_set(CLOCK_KV, self.clock.offset_s)

    # ---------- lifecycle ----------
    async def start(self) -> None:
        self.kernel.load()
        self.kernel.start()
        if self.hub is not None:
            self.hub.start()
        for fn in self.on_start:
            await _maybe_await(fn(self))

    async def stop(self) -> None:
        for fn in self.on_stop:
            try:
                await _maybe_await(fn(self))
            except Exception:
                log.exception("stop hook failed")
        if self.hub is not None:
            await self.hub.stop()
        await self.kernel.stop()
        self.db.close()


async def _maybe_await(r: Any) -> Any:
    if inspect.isawaitable(r):
        return await r
    return r


def _install_reducers(svc: Services) -> None:
    kw = _import_optional("ahbo.kernel.wiring")
    if kw is not None and hasattr(kw, "install"):
        kw.install(svc)
        svc.installed.append("kernel")
        if getattr(kw, "router", None) is not None:
            svc.routers.append(kw.router)
    else:
        from ahbo.kernel.reducers import register_all
        svc.reducers = register_all(svc.kernel)
    svc.reducers = svc.reducers or [n for n, _ in svc.kernel.reducers]


def _build_agent(svc: Services) -> None:
    aw = _import_optional("ahbo.agent.wiring")
    built = None
    if aw is not None and hasattr(aw, "build_agent"):
        try:
            built = aw.build_agent(svc.kernel)
        except Exception:
            log.exception("agent.wiring.build_agent failed; using the default text loop")
    if built is not None:
        if isinstance(built, tuple):
            svc.bridge, svc.text_loop = built[0], built[1]
        elif isinstance(built, dict):
            svc.bridge, svc.text_loop = built.get("bridge"), built.get("text_loop")
            svc.agent = built.get("agent")
        else:
            svc.bridge = getattr(built, "bridge", None)
            svc.text_loop = getattr(built, "text_loop", None)
            svc.agent = built
    if svc.bridge is None or svc.text_loop is None:
        from ahbo.agent.bridge import AgentBridge
        from ahbo.agent.runner import TurnAgent
        from ahbo.agent.text_loop import TextLoop
        svc.bridge = svc.bridge or AgentBridge(svc.kernel)
        svc.text_loop = TextLoop(svc.bridge, TurnAgent(svc.bridge, _flash(svc.settings)))


def _flash(settings: Settings) -> Any:
    from ahbo.models.fakes import FakeFlash
    if settings.is_fake("models"):
        return FakeFlash()
    mod = _import_optional("ahbo.models.flash")
    for name in ("build_flash", "make_flash"):
        if mod is not None and hasattr(mod, name):
            return getattr(mod, name)(settings)
    log.warning("real Flash requested but ahbo.models.flash has no builder; using FakeFlash")
    return FakeFlash()


def build_services(settings: Settings | None = None, *, db: Database | None = None, db_path: str | Path | None = None,
                   clock: Clock | None = None, config: Config | None = None, time_scale: float = 1.0,
                   with_hub: bool = True, seed_if_empty: bool = True, install_components: bool = True) -> Services:
    settings = settings or get_settings()
    if db is None:
        path = db_path if db_path is not None else settings.db_path
        db = open_db(path)
    else:
        db.migrate()
    if clock is None:
        clock = get_clock()
        offset = db.kv_get(CLOCK_KV)
        if offset is not None:
            clock.set_offset(float(offset))
    set_clock(clock)
    config = config or load_config()
    kernel = Kernel(db=db, clock=clock, config=config, settings=settings, time_scale=time_scale)
    svc = Services(settings=settings, db=db, clock=clock, config=config, kernel=kernel)

    _install_reducers(svc)
    _build_agent(svc)
    if with_hub:
        from ahbo.links.device_hub import DeviceHub
        svc.hub = DeviceHub(svc)
    if install_components:
        for pkg in COMPONENTS:
            if pkg == "kernel":
                continue
            mod = _import_optional(f"ahbo.{pkg}.wiring")
            if mod is None:
                continue
            try:
                if hasattr(mod, "install"):
                    mod.install(svc)
                svc.installed.append(pkg)
                if getattr(mod, "router", None) is not None:
                    svc.routers.append(mod.router)
            except Exception:
                log.exception("wiring.install failed for %s", pkg)

    if seed_if_empty and db.one("SELECT id FROM resident LIMIT 1") is None:
        from ahbo.ops.seed import load_demo
        load_demo(db, clock, settings)
        log.info("seeded the demo (empty database)")
    ensure_fixed_manifest(svc)
    return svc


# ---------- fixed clips (§10.3) ----------
def fixed_dir(svc: Services) -> Path:
    rf = _import_optional("ahbo.ops.render_fixed")
    fn = getattr(rf, "fixed_dir", None) if rf else None
    if callable(fn):
        try:
            return Path(fn(svc.settings))
        except TypeError:
            return Path(fn())
    return svc.settings.media_dir / "fixed"


def ensure_fixed_manifest(svc: Services) -> None:
    rf = _import_optional("ahbo.ops.render_fixed")
    if rf is not None and hasattr(rf, "ensure_manifest"):
        try:
            rf.ensure_manifest(svc)
            return
        except Exception:
            log.exception("render_fixed.ensure_manifest failed; writing a text-only fallback manifest")
    _fallback_manifest(svc)


def _fallback_manifest(svc: Services) -> None:
    """Text-only manifest (no audio) so the device channel has a version until `render-fixed` runs."""
    import hashlib

    from ahbo.core.jsonutil import dumps
    path = fixed_dir(svc) / "manifest.json"
    if path.exists():
        return
    clips: dict[str, Any] = {}
    for cid, text in svc.config.fixed_lines.items():
        if cid.endswith("_TEMPLATE"):
            continue
        dur = round(max(1.0, len(text) * 0.19), 2)
        clips[cid] = {"file": f"{cid}.m4a", "text": text, "segments": [[0, len(text), 0.0, dur]], "duration": dur}
    ver = "f-" + hashlib.sha1(dumps(clips).encode()).hexdigest()[:12]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps({"version": ver, "clips": clips, "fallback": True}), encoding="utf-8")


def read_manifest(svc: Services) -> dict[str, Any]:
    from ahbo.core.jsonutil import loads
    path = fixed_dir(svc) / "manifest.json"
    try:
        return loads(path.read_text(encoding="utf-8"), {}) or {}
    except (OSError, ValueError):
        return {}
