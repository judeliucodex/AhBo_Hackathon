"""Links: the family (Telegram), phone (Twilio) and beacon-sensor integrations (§9.1–9.3).

Each channel has a real and a fake implementation, chosen at start-up by `settings.is_fake("telegram"|"twilio")`
(§26.3). Every outbound message or call is recorded in the `outbox` table; results come back as Kernel events.

`wiring.install(svc)` registers the `family.send` and `phone.call` dispatchers and exposes `wiring.router`
(Telegram webhook, Twilio callbacks, sensor ingest). Route handlers find the services object through
`current_svc(request)`: `request.app.state.svc` if the platform set it, else the one passed to `install`.
"""
from __future__ import annotations

from typing import Any

_registry: dict[str, Any] = {}


def current_svc(request: Any = None) -> Any:
    if request is not None:
        svc = getattr(getattr(getattr(request, "app", None), "state", None), "svc", None)
        if svc is not None:
            return svc
    svc = _registry.get("svc")
    if svc is None:
        raise RuntimeError("links not installed (call ahbo.links.wiring.install(svc) first)")
    return svc
