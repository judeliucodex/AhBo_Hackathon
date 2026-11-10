"""Helpers for the system-level invariant tests (§2, §16.3 A1). The system is `ahbo.evals.harness.EvalSystem`: the
real Kernel with every reducer and component wiring, in-memory DB, FakeClock, recording device, text loop."""
from __future__ import annotations

from typing import Any

import pytest

from ahbo.evals.harness import EvalSystem

FALL_STATES_OK = ("RESOLVED_OK",)


def need(sys_: EvalSystem, *needs: str) -> None:
    """Skip (never pass) when a component the test exercises is not built yet."""
    missing = [n for n in needs if not sys_.has(n)]
    if missing:
        pytest.skip(f"component not built yet: {', '.join(missing)}")


async def fall(sys_: EvalSystem, source: str = "beacon") -> None:
    if source == "operator":
        await sys_.event("operator.fall", "operator")
    else:
        await sys_.event("sensor.fall_suspected", source, beacon_id="beacon1", peak_g=3.1, freefall_ms=180,
                         stillness_s=2.4)


async def until_state(sys_: EvalSystem, state: str, timeout: float = 40.0) -> bool:
    return await sys_.wait_for(lambda: state in sys_.incident_path, timeout)


async def until_ui(sys_: EvalSystem, ui_type: str, timeout: float = 30.0) -> bool:
    return await sys_.wait_for(lambda: any(u.get("type") == ui_type for u in sys_.device.ui()), timeout)


def played(sys_: EvalSystem) -> list[str]:
    return [x.get("clip_id") for x in sys_.device.sent if x["type"] == "device.play_fixed"]


def cmds(sys_: EvalSystem, type_: str) -> list[dict[str, Any]]:
    return sys_.commands(type_)


def incident_id(sys_: EvalSystem) -> str | None:
    inc = sys_.kernel.state.incident
    if inc is not None:
        return inc.get("id") if isinstance(inc, dict) else getattr(inc, "id", None)
    row = sys_.db.one("SELECT id FROM incidents ORDER BY rowid DESC LIMIT 1")
    return row["id"] if row else None
