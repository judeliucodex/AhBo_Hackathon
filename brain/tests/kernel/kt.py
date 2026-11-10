"""Helpers for the Kernel flow tests (uses the shared `harness` fixture from tests/conftest.py)."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from ahbo.core.events import Event


def watch(h) -> None:
    """Record every command and the incident state path on the harness."""
    h.cmds, h.path = [], []

    def listener(kind: str, obj: dict[str, Any]) -> None:
        if kind == "command":
            h.cmds.append(obj)
        inc = h.kernel.state.incident
        st = inc.get("state") if isinstance(inc, dict) else None
        if st and (not h.path or h.path[-1] != st):
            h.path.append(st)

    h.kernel.subscribe(listener)


def of(h, type_: str, **match: Any) -> list[dict[str, Any]]:
    return [c for c in h.cmds if c["type"] == type_ and all(c["payload"].get(k) == v for k, v in match.items())]


def faces(h, ftype: str | None = None) -> list[dict[str, Any]]:
    out = [c["payload"]["ui"] for c in h.cmds if c["type"] == "device.ui"]
    return [u for u in out if ftype is None or u.get("type") == ftype]


def clips(h) -> list[str]:
    return [c["payload"]["clip_id"] for c in of(h, "device.play_fixed")]


async def until(pred: Callable[[], bool], timeout: float = 5.0) -> bool:
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if pred():
            return True
        await asyncio.sleep(0.01)
    return pred()


async def ev(h, type_: str, source: str = "operator", corr: str | None = None, **payload: Any) -> list:
    return await h.kernel.process(Event(type=type_, source=source, correlation_id=corr, payload=payload))


async def req(h, type_: str, _source: str = "agent", **payload: Any) -> dict[str, Any] | None:
    return await h.kernel.request(Event(type=type_, source=_source, payload=payload))


def inc(h) -> dict[str, Any]:
    return h.kernel.state.incident


async def timer(h, key: str, **data: Any) -> list:
    """Fire a Kernel timer now (instead of waiting for it)."""
    h.kernel.cancel_timer(key)
    return await ev(h, "timer.fired", "system", key=key, data=data)
