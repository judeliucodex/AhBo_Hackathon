"""The Kernel loop (§4.1): append event → run reducers → log + dispatch commands. Single writer of state.

Reducers are plain functions `reducer(state, event, ctx) -> list[Command] | None`. They read the Kernel state,
mutate only their own slice of it, and return commands. They never do I/O: anything that touches the outside
world is a command, dispatched to an adapter. Command results come back as events (§4.1 step 5).

Timers are commands too (`timer.set {key, delay_s, data}` / `timer.cancel {key}`); when one fires the Kernel
processes `timer.fired {key, data}`. Durations use the event loop's monotonic clock scaled by `time_scale`
(tests run fast with a small scale); the virtual clock offset never stretches them.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ahbo.core.clock import Clock
from ahbo.core.config import Config
from ahbo.core.db import Database
from ahbo.core.events import Command, Event
from ahbo.core.jsonutil import dumps
from ahbo.core.settings import Settings

from .state import KernelState
from .ui_state import UIState

log = logging.getLogger("ahbo.kernel")

Reducer = Callable[[KernelState, Event, "Ctx"], list[Command] | None]
Dispatcher = Callable[[Command], Awaitable[None] | None]
Listener = Callable[[str, dict[str, Any]], None]


@dataclass
class Ctx:
    """What a reducer may read besides the state. No I/O through it except read-only DB queries."""
    kernel: Kernel
    now: datetime
    config: Config
    settings: Settings
    db: Database

    @property
    def profile(self) -> str:
        return self.settings.profile

    @property
    def ui(self) -> UIState:
        return self.kernel.ui

    def reply(self, ev: Event, result: dict[str, Any]) -> None:
        """Answer a request event (see Kernel.request): the caller gets `result` back."""
        self.kernel.state.extra.setdefault("_results", {})[ev.id] = result


class Kernel:
    def __init__(self, *, db: Database, clock: Clock, config: Config, settings: Settings,
                 time_scale: float = 1.0):
        self.db, self.clock, self.config, self.settings = db, clock, config, settings
        self.time_scale = time_scale
        self.state = KernelState()
        self.ui = UIState(self)
        self.reducers: list[tuple[str, Reducer]] = []
        self.dispatchers: dict[str, Dispatcher] = {}
        self.listeners: list[Listener] = []
        self.queue: asyncio.Queue[Event] = asyncio.Queue()
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._bg: set[asyncio.Task] = set()
        self.persist_hooks: list[Callable[[Kernel], None]] = []
        self.loaded_hooks: list[Callable[[Kernel], None]] = []

    # ---------- wiring ----------
    def add_reducer(self, name: str, fn: Reducer) -> None:
        self.reducers.append((name, fn))

    def on(self, key: str, fn: Dispatcher) -> None:
        """Register a dispatcher for a command type (`device.ui`) or a target (`device`)."""
        self.dispatchers[key] = fn

    def subscribe(self, fn: Listener) -> Callable[[], None]:
        self.listeners.append(fn)
        return lambda: self.listeners.remove(fn) if fn in self.listeners else None

    def ctx(self) -> Ctx:
        return Ctx(kernel=self, now=self.clock.now(), config=self.config, settings=self.settings, db=self.db)

    # ---------- lifecycle ----------
    def load(self) -> None:
        """Rebuild in-memory state from the DB after a restart (each flow registers a loaded hook)."""
        self.ui.load()
        for hook in self.loaded_hooks:
            try:
                hook(self)
            except Exception:  # a broken loader must not stop the Kernel coming up
                log.exception("loaded hook failed")

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="kernel")

    async def stop(self) -> None:
        for h in self._timers.values():
            h.cancel()
        self._timers.clear()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        for t in list(self._bg):
            t.cancel()

    async def _run(self) -> None:
        while True:
            ev = await self.queue.get()
            try:
                await self.process(ev)
            except Exception:
                log.exception("kernel failed to process %s", ev.type)

    # ---------- input ----------
    async def submit(self, ev: Event) -> None:
        await self.queue.put(ev)

    def submit_nowait(self, ev: Event) -> None:
        self.queue.put_nowait(ev)

    async def process(self, ev: Event) -> list[Command]:
        """Process one event now (serialised with every other event and emit)."""
        async with self._lock:
            return await self._process_locked(ev)

    async def _process_locked(self, ev: Event) -> list[Command]:
        self._record_event(ev)
        self._notify("kernel_event", ev.model_dump())
        ctx = self.ctx()
        out: list[Command] = []
        for name, fn in self.reducers:
            try:
                cmds = fn(self.state, ev, ctx) or []
            except Exception as e:  # a reducer bug is logged and traced, never crashes the loop
                log.exception("reducer %s failed on %s", name, ev.type)
                self._record_quality("reducer_error", f"{name}: {e!r}", ev.correlation_id)
                cmds = []
            for c in cmds:
                c.event_id = c.event_id or ev.id
                if c.correlation_id is None:
                    c.correlation_id = ev.correlation_id
                out.append(c)
                await self._emit_locked(c)
        self._persist()
        return out

    async def request(self, ev: Event) -> dict[str, Any] | None:
        """Process an event and return the result a reducer gave with ctx.reply(ev, …) (tools use this)."""
        async with self._lock:
            await self._process_locked(ev)
            return self.state.extra.get("_results", {}).pop(ev.id, None)

    async def emit(self, *cmds: Command) -> None:
        """Emit commands outside of event processing (e.g. replay). Serialised with process()."""
        async with self._lock:
            for c in cmds:
                await self._emit_locked(c)
            self._persist()

    async def _emit_locked(self, c: Command) -> None:
        if c.type == "device.ui":
            if not self.ui.observe(c):
                return  # e.g. a mood that is already showing
        self._record_command(c)
        self._notify("command", c.model_dump())
        if c.type == "device.ui":
            self._notify("ui", c.payload["ui"])
        if c.type == "timer.set":
            self._set_timer(c.payload["key"], float(c.payload.get("delay_s", 0)), c.payload.get("data") or {},
                            c.correlation_id)
            return
        if c.type == "timer.cancel":
            self.cancel_timer(c.payload["key"])
            return
        fn = self.dispatchers.get(c.type) or self.dispatchers.get(c.target)
        if fn is None:
            return
        try:
            r = fn(c)
            if inspect.isawaitable(r):
                await r
        except Exception as e:
            log.exception("dispatcher failed for %s", c.type)
            self.db.execute("UPDATE commands SET status='failed', detail=? WHERE id=?", (repr(e)[:500], c.id))

    # ---------- timers ----------
    def _set_timer(self, key: str, delay_s: float, data: dict[str, Any], corr: str | None) -> None:
        self.cancel_timer(key)
        loop = asyncio.get_running_loop()

        def fire() -> None:
            self._timers.pop(key, None)
            self.submit_nowait(Event(type="timer.fired", source="system", correlation_id=corr,
                                     payload={"key": key, "data": data}))

        self._timers[key] = loop.call_later(max(0.0, delay_s * self.time_scale), fire)

    def cancel_timer(self, key: str) -> None:
        h = self._timers.pop(key, None)
        if h:
            h.cancel()

    def timer_pending(self, key: str) -> bool:
        return key in self._timers

    def spawn(self, coro: Awaitable[Any], name: str = "bg") -> asyncio.Task:
        """Run adapter work in the background (dispatchers must not block the Kernel)."""
        t = asyncio.ensure_future(coro)
        self._bg.add(t)

        def done(task: asyncio.Task) -> None:
            self._bg.discard(task)
            if not task.cancelled() and task.exception():
                log.error("background task %s failed: %r", name, task.exception())

        t.add_done_callback(done)
        return t

    # ---------- recording (INV-6) ----------
    def _record_event(self, ev: Event) -> None:
        self.db.execute("INSERT OR IGNORE INTO events(id, ts, type, source, correlation_id, payload) VALUES (?,?,?,?,?,?)",
                        (ev.id, ev.ts, ev.type, ev.source, ev.correlation_id, dumps(ev.payload)))

    def _record_command(self, c: Command) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO commands(id, event_id, ts, type, target, priority, correlation_id, payload, status) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (c.id, c.event_id, self.clock.iso(), c.type, c.target, c.priority, c.correlation_id, dumps(c.payload),
             "pending"))

    def _record_quality(self, kind: str, detail: str, corr: str | None = None) -> None:
        from ahbo.core.ids import new_id
        self.db.execute("INSERT INTO quality(id, ts, kind, detail, correlation_id) VALUES (?,?,?,?,?)",
                        (new_id("q"), self.clock.iso(), kind, detail, corr))

    def mark_command(self, cmd_id: str, status: str, detail: str | None = None) -> None:
        self.db.execute("UPDATE commands SET status=?, acked_at=CASE WHEN ?='acked' THEN ? ELSE acked_at END, "
                        "detail=COALESCE(?, detail) WHERE id=?", (status, status, self.clock.iso(), detail, cmd_id))

    def _persist(self) -> None:
        self.ui.save()
        for hook in self.persist_hooks:
            try:
                hook(self)
            except Exception:
                log.exception("persist hook failed")

    def _notify(self, kind: str, obj: dict[str, Any]) -> None:
        for fn in list(self.listeners):
            try:
                fn(kind, obj)
            except Exception:
                log.exception("listener failed")

    # ---------- helpers ----------
    @staticmethod
    def monotonic() -> float:
        return time.monotonic()
