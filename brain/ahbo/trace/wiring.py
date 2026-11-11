"""trace wiring: default DB for spans/costs, and the cap-exceeded notifier (an informational Kernel event)."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("ahbo.trace")


def install(svc: Any) -> None:
    from ahbo.core.events import Event
    from ahbo.ops.caps import set_notifier

    from . import spans

    spans.set_default_db(svc.db)

    def notify(detail: dict[str, Any]) -> None:
        # informational event (INV-6, INV-8): recorded and shown on the console, no reducers involved
        svc.kernel.note(Event(type="system.cap_exceeded", source="system", payload=detail))

    set_notifier(notify)
