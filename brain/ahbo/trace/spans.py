"""Trace spans (§15) stored in the `spans` table, keyed by correlation id (session, incident, dose, action).

    with span(db, "tool.get_med_status", correlation_id=session_id, turn_id=turn_id):
        ...
    async with span(db, "telegram.send", correlation_id=incident_id): ...

    @traced("deep.step")                 # uses the default DB set by trace.wiring.install
    async def step(...): ...

Turn latency (§15): `TurnTimer` marks eos → stt_final → llm_ttft → tts_first_audio and writes one
`turn.latency` span (+ `turns.latency` JSON when the turn row exists). `latency_stats` gives p50/p95.
Recording never raises into the caller: a trace failure is logged and dropped.
"""
from __future__ import annotations

import functools
import inspect
import logging
import time
from collections.abc import Callable
from typing import Any

from ahbo.core.clock import Clock, get_clock
from ahbo.core.db import Database
from ahbo.core.ids import new_id
from ahbo.core.jsonutil import dumps, loads

log = logging.getLogger("ahbo.trace")

_default_db: Database | None = None
TURN_STAGES = ("eos", "stt_final", "llm_ttft", "tts_first_audio")


def set_default_db(db: Database | None) -> None:
    global _default_db
    _default_db = db


def get_default_db() -> Database | None:
    return _default_db


def record_span(db: Database | None, name: str, *, correlation_id: str | None = None, start_ts: str | None = None,
                end_ts: str | None = None, duration_ms: float | None = None, status: str = "ok",
                attrs: dict[str, Any] | None = None, clock: Clock | None = None) -> str | None:
    db = db or _default_db
    if db is None:
        return None
    clock = clock or get_clock()
    sid = new_id("span")
    try:
        db.execute("INSERT INTO spans(id, correlation_id, name, start_ts, end_ts, duration_ms, status, attrs) "
                   "VALUES (?,?,?,?,?,?,?,?)",
                   (sid, correlation_id, name, start_ts or clock.iso(), end_ts or clock.iso(),
                    None if duration_ms is None else round(float(duration_ms), 1), status, dumps(attrs or {})))
    except Exception:
        log.exception("could not record span %s", name)
        return None
    return sid


class span:
    """Context manager (sync or async) recording one span. `s.attrs` can be extended inside the block;
    an exception marks the span `error` (and is re-raised)."""

    def __init__(self, db: Database | None, name: str, correlation_id: str | None = None, *,
                 clock: Clock | None = None, **attrs: Any):
        self.db, self.name, self.correlation_id = db, name, correlation_id
        self.clock = clock or get_clock()
        self.attrs: dict[str, Any] = dict(attrs)
        self.status = "ok"
        self.id: str | None = None

    def __enter__(self) -> span:
        self._start_ts = self.clock.iso()
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self.status = "error"
            self.attrs.setdefault("error", exc_type.__name__)
        dur = (time.perf_counter() - self._t0) * 1000
        self.id = record_span(self.db, self.name, correlation_id=self.correlation_id, start_ts=self._start_ts,
                              duration_ms=dur, status=self.status, attrs=self.attrs, clock=self.clock)
        return False

    async def __aenter__(self) -> span:
        return self.__enter__()

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return self.__exit__(exc_type, exc, tb)


def traced(name: str, *, correlation_arg: str = "correlation_id", db: Callable[[], Database | None] | None = None):
    """Decorator: record a span around a sync or async function. The correlation id is taken from the keyword
    argument `correlation_arg` when present. Uses the default DB unless `db` (a getter) is given."""
    def deco(fn):
        def corr(kwargs: dict[str, Any]) -> str | None:
            v = kwargs.get(correlation_arg)
            return str(v) if v else None

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def aw(*args, **kwargs):
                async with span(db() if db else _default_db, name, corr(kwargs)):
                    return await fn(*args, **kwargs)
            return aw

        @functools.wraps(fn)
        def w(*args, **kwargs):
            with span(db() if db else _default_db, name, corr(kwargs)):
                return fn(*args, **kwargs)
        return w
    return deco


class TurnTimer:
    """Marks the voice-turn stages with a monotonic clock (`mark("eos")` …) and writes the latency log."""

    def __init__(self, turn_id: str, session_id: str | None = None):
        self.turn_id, self.session_id = turn_id, session_id
        self.marks: dict[str, float] = {}

    def mark(self, stage: str, at: float | None = None) -> None:
        if stage not in TURN_STAGES:
            raise ValueError(f"unknown turn stage {stage!r}")
        self.marks.setdefault(stage, time.monotonic() if at is None else at)

    def latency(self) -> dict[str, float]:
        """Milliseconds: each stage relative to eos, the gaps between stages, and total (eos → first audio)."""
        m = self.marks
        out: dict[str, float] = {}
        base = m.get("eos")
        prev: tuple[str, float] | None = None
        for st in TURN_STAGES:
            if st not in m:
                continue
            if base is not None and st != "eos":
                out[f"{st}_ms"] = round((m[st] - base) * 1000, 1)
            if prev is not None:
                out[f"{prev[0]}_to_{st}_ms"] = round((m[st] - prev[1]) * 1000, 1)
            prev = (st, m[st])
        if base is not None and "tts_first_audio" in m:
            out["total_ms"] = round((m["tts_first_audio"] - base) * 1000, 1)
        return out

    def finish(self, db: Database | None = None, **attrs: Any) -> dict[str, float]:
        lat = self.latency()
        db = db or _default_db
        record_span(db, "turn.latency", correlation_id=self.session_id, duration_ms=lat.get("total_ms"),
                    attrs={"turn_id": self.turn_id, **lat, **attrs})
        if db is not None:
            try:
                db.execute("UPDATE turns SET latency=? WHERE id=?", (dumps(lat), self.turn_id))
            except Exception:
                log.exception("could not store turn latency")
        return lat


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank-with-interpolation percentile (p in 0..100)."""
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 1)


def latency_stats(db: Database, n: int = 50) -> dict[str, Any]:
    """p50/p95 over the last `n` turns that reached first audio (`ahbo doctor --latency`)."""
    rows = db.query("SELECT attrs FROM spans WHERE name='turn.latency' AND duration_ms IS NOT NULL "
                    "ORDER BY start_ts DESC, rowid DESC LIMIT ?", (n,))
    attrs = [loads(r["attrs"], {}) for r in rows]
    out: dict[str, Any] = {"count": len(attrs)}
    for key in ("total_ms", "stt_final_ms", "llm_ttft_ms", "eos_to_stt_final_ms", "stt_final_to_llm_ttft_ms",
                "llm_ttft_to_tts_first_audio_ms"):
        vals = [float(a[key]) for a in attrs if isinstance(a.get(key), (int, float))]
        out[key] = {"p50": percentile(vals, 50), "p95": percentile(vals, 95), "n": len(vals)}
    return out
