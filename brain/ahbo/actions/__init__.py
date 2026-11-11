"""Actions (§4.3.5, §8.3): the external-action runner — Playwright sign-ups against the allowlisted mock
centre and template Telegram messages — plus the mock centre's registration API (§26.4).

The Kernel emits `action.run {action_id}` only after 王婆婆's recorded yes (D12/D13, INV-1); `wiring.install`
registers the dispatcher, which returns fast and does the work with `kernel.spawn`, reporting back with
`action.status {action_id, status, screenshots, detail}` events (CONTRACTS §4.1). Every module here obeys
data minimisation: only `resident.shareable` fields are ever typed into a page, and a forbidden field
(HKID / card / password / bank, date of birth unless shareable) or any unknown required field is a hard stop
(`needs_human`). Page text is untrusted data (INV-7): it can never change what is filled.

Modules: `domains` (allowlist + internal-URL rewrite) · `field_map` (labels → shareable fields only) ·
`playwright_form` (the browser session) · `runner` (the `action.run` dispatcher) · `telegram_msg`
(fixed-template messages) · `mock_centre` (`/mock-centre/api/*`) · `activities` (seeded offer).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Outcome:
    """What a runner method produced; `runner` turns it into one terminal `action.status` event."""

    status: str                       # done | needs_human | failed
    detail: dict[str, Any] = field(default_factory=dict)
    screenshots: list[str] = field(default_factory=list)
