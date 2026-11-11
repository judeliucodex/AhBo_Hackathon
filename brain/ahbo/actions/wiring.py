"""Actions component wiring (AGENT_BRIEF convention).

- `install(svc)`: creates the mock centre's table (`mock_registrations`) and registers the `action.run`
  dispatcher. The dispatcher returns immediately; the runner works in a background task
  (`kernel.spawn`) and reports back with `action.status` events (CONTRACTS §4.1).
- `router`: the mock centre's API routes (`POST /mock-centre/api/register`, `GET /mock-centre/api/activities`).
  The platform's app includes it; the static page under `/mock-centre/` is served by
  `ahbo.api.routes.public` (BUILD_LOG D-3), which is added last so these API routes win.
"""
from __future__ import annotations

from typing import Any

from . import mock_centre, runner

router = mock_centre.router


def install(svc: Any) -> None:
    mock_centre.bind(svc.db)

    def on_action_run(c: Any) -> None:
        svc.kernel.spawn(runner.run_action(svc.kernel, c), name="action.run")

    svc.kernel.on("action.run", on_action_run)
