"""Reducers, one module per flow (§4.3). `register_all` wires them into a Kernel in a fixed order:
safety first (fall, concern), then care (dose, checkin), then the conversational and UI flows; the arbiter runs
after session/turn so it sees the session and turn state the event produced; `misc` (console/device bookkeeping) last.

Flow modules that are not built yet are skipped (ImportError), so the Kernel always starts.
"""
from __future__ import annotations

import importlib
import logging

log = logging.getLogger("ahbo.kernel")

ORDER = ("fall", "concern", "dose", "checkin", "action", "session", "turn", "voice", "arbiter", "misc")


def register_all(kernel) -> list[str]:
    loaded = []
    for name in ORDER:
        try:
            mod = importlib.import_module(f"{__name__}.{name}")
        except ModuleNotFoundError as e:
            if e.name and e.name.endswith(f"reducers.{name}"):
                continue
            raise
        if hasattr(mod, "setup"):
            mod.setup(kernel)          # optional: register persist/loaded hooks, dispatchers
        if hasattr(mod, "reduce"):
            kernel.add_reducer(name, mod.reduce)
            loaded.append(name)
    return loaded
