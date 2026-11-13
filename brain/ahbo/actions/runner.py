"""The Action Runner dispatcher (§8.3): the `action.run {action_id}` command → method runner →
`action.status {action_id, status, screenshots, detail}` events (CONTRACTS §4.1).

Dispatchers must return fast, so `wiring.install` spawns `run_action` as a background task. The flow for
`playwright_form`: read the action row (the Kernel sets state `running` only after her recorded yes) →
cap check (`playwright_actions`, §11: 10/day; `ahbo.ops.caps` is imported lazily, it may be absent) →
domain allowlist on the PUBLIC url (`config/action_domains.yaml`; `read_only` domains are never submitted) →
rewrite the allowlisted public url to the api's internal one (`domains.navigation_url`, module doc) →
emit `filling` → `playwright_form.submit_form` (≤ 110 s; the Kernel's own 120 s guard declares the
`failed` timeout) → record the attempt in `costs` (provider `playwright`, what the cap reads) → emit
`done` / `needs_human` / `failed`. For `telegram_message` the template send goes through the Kernel event
`action.message_send` (see `telegram_msg`).
"""
from __future__ import annotations

import asyncio
import logging
import re
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from ahbo.core.events import Command, Event
from ahbo.core.jsonutil import loads

from . import Outcome, activities, domains, playwright_form, telegram_msg
from . import field_map as fm

log = logging.getLogger("ahbo.actions.runner")

RUN_BUDGET_S = 110.0            # under the Kernel's 120 s action:timeout guard (§4.3.5)
TIME_RE = re.compile(r"\d{1,2}:\d{2}")


def shareable_of(kernel: Any) -> dict[str, Any]:
    r = kernel.db.one("SELECT shareable FROM resident WHERE id='resident'")
    return (loads(r["shareable"], {}) if r and r.get("shareable") else None) or {}


def record_playwright_cost(kernel: Any, action_id: str) -> None:
    """Every attempt counts against the §11 cap (`spend_today` reads costs rows, provider `playwright`)."""
    try:
        from ahbo.trace.costs import record_cost
        record_cost(kernel.db, "playwright", None, {}, 0.0, correlation_id=action_id, purpose="playwright_form",
                    clock=kernel.clock)
    except Exception:
        log.exception("could not record the playwright cost row")


async def run_action(kernel: Any, c: Command) -> None:
    """The `action.run` dispatcher body. Never raises: a crash becomes a `failed` action.status."""
    action_id = str((c.payload or {}).get("action_id") or "")
    try:
        row = kernel.db.one("SELECT * FROM actions WHERE id=?", (action_id,))
        if row is None or row["state"] != "running":
            log.warning("action.run for a missing or non-running action %s", action_id)
            return
        details = loads(row["details"], {}) or {}
        shareable = shareable_of(kernel)
        method = row["method"] or ""
        if method == "telegram_message":
            action = {"id": action_id, "target": row["target"], "shareable": shareable,
                      "title": details.get("title") or row["target"]}
            outcome = await telegram_msg.run(kernel, action, details)
        elif method == "playwright_form":
            outcome = await run_playwright(kernel, action_id, details, shareable)
        elif method == "handoff":
            # e.g. a seeded activity whose signup is in_person/phone (§4.3.5): nothing runs, 阿珍 takes over
            outcome = Outcome("needs_human", {"reason": "method_handoff", "method": method})
        else:
            outcome = Outcome("needs_human", {"reason": "unknown_method", "method": method})
    except Exception as e:
        log.exception("action.run failed for %s", action_id)
        outcome = Outcome("failed", {"reason": "internal_error", "error": repr(e)[:200]})
    await _status(kernel, action_id, outcome.status, outcome.detail, outcome.screenshots)


async def run_playwright(kernel: Any, action_id: str, details: dict[str, Any],
                         shareable: dict[str, Any]) -> Outcome:
    url, reason = _target_url(kernel, details)
    if reason:
        return Outcome("failed", {"reason": reason, "url": url})
    verdict = domains.classify_url(url, kernel.config.action_domains)
    if verdict == "read_only":
        return Outcome("needs_human", {"reason": "read_only_domain", "url": url})
    if verdict != "submit":
        return Outcome("needs_human", {"reason": "domain_not_allowed", "url": url})

    ok, used, cap = _check_cap(kernel)
    if not ok:
        return Outcome("failed", {"reason": "daily_cap", "used": used, "cap": cap})

    nav = domains.navigation_url(url, kernel.config.action_domains, kernel.settings.public_base_url,
                                 domains.internal_base(kernel.settings))
    hhmm = str(details.get("time") or "")
    job = playwright_form.Job(
        action_id=action_id, url=url, navigate_to=nav, shareable=shareable,
        choice=fm.ActionChoice(activity_id=details.get("activity_id") or None,
                               activity_title=details.get("title") or None, time=hhmm or None,
                               session_value=activities.session_value(hhmm) if TIME_RE.fullmatch(hhmm) else None),
        forbidden_terms=list(kernel.config.action_domains.get("forbidden_fields") or []),
        shots_dir=kernel.settings.media_dir / "actions" / action_id,
        budget_s=RUN_BUDGET_S, llm=None if kernel.settings.is_fake("models") else _SonnetFieldMap(kernel))

    await _status(kernel, action_id, "filling", {"url": url, "navigate_to": nav})
    try:
        outcome = await asyncio.wait_for(playwright_form.submit_form(job, kernel.settings),
                                         timeout=RUN_BUDGET_S + 10.0)
    except TimeoutError:
        outcome = Outcome("failed", {"reason": "timeout"})
    if outcome.status == "failed" and outcome.detail.get("reason") == "browser_error" \
            and not await browser_reachable(kernel.settings.browser_ws):
        # No browser container (e.g. evals/tests without it): nothing was opened or submitted, so 阿珍 finishes
        # the sign-up by hand (HANDOFF via the Kernel's normal needs_human path) instead of a hard failure.
        return Outcome("needs_human", {"reason": "browser_unavailable", "url": url,
                                       "error": str(outcome.detail.get("error") or "")[:200]}, outcome.screenshots)
    record_playwright_cost(kernel, action_id)
    return outcome


async def browser_reachable(ws_url: str, timeout: float = 2.0) -> bool:
    """Can we open a TCP connection to the Playwright server (`settings.browser_ws`)? Used only after a
    `browser_error`, to tell "no browser container" (→ needs_human) from a failure inside a real run (→ failed)."""
    parts = urlsplit(ws_url or "")
    host = parts.hostname
    if not host:
        return False
    port = parts.port or (443 if parts.scheme in ("wss", "https") else 80)
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, TimeoutError, ValueError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


def _target_url(kernel: Any, details: dict[str, Any]) -> tuple[str, str | None]:
    url = str(details.get("url") or "")
    if not url:
        act = activities.find_activity(details.get("activity_id"), kernel.settings.public_base_url) or {}
        url = str((act.get("signup") or {}).get("url") or "")
    if not url:
        return "", "no_url"
    return url, None


def _check_cap(kernel: Any) -> tuple[bool, float, float]:
    """(ok, used, cap) for `playwright_actions`; a missing/odd caps module never blocks the action."""
    try:
        from ahbo.ops.caps import check_cap
        return check_cap(kernel.db, kernel.clock, kernel.config, "playwright_actions")
    except Exception:
        log.exception("caps check failed; allowing the action")
        return True, -1.0, -1.0


async def _status(kernel: Any, action_id: str, status: str, detail: dict[str, Any],
                  screenshots: list[str] | None = None) -> None:
    await kernel.submit(Event(type="action.status", source="action", correlation_id=action_id,
                              payload={"action_id": action_id, "status": status,
                                       "screenshots": list(screenshots or []), "detail": detail}))


class _SonnetFieldMap:
    """`field_map.FieldMapLLM` over the §20.2 Sonnet config (purpose `signup_fieldmap`). Real mode only —
    fake mode (the default) always maps deterministically. The model only proposes field *names*; the
    values still come from `resident.shareable` and are re-validated in `field_map.build_plan`."""

    def __init__(self, kernel: Any) -> None:
        self._k = kernel

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, *,
                   purpose: str = "signup_fieldmap", thinking: str | None = None,
                   correlation_id: str | None = None) -> Any:
        from ahbo.models import sonnet
        cfg = sonnet.sonnet_cfg(self._k.config, purpose, thinking)
        kw = sonnet.request_kwargs(cfg, system=messages[0]["content"], tools=[],
                                   messages=[{"role": "user", "content": messages[1]["content"]}], schema=None)
        client = sonnet.make_client(self._k.settings)
        async with client.beta.messages.stream(**kw) as stream:
            msg = await stream.get_final_message()
        text = "".join(getattr(b, "text", "") for b in (msg.content or []))
        usage = getattr(msg, "usage", None)
        try:
            from ahbo.models.pricing import usd
            from ahbo.trace.costs import record_cost
            units = {"input_tokens": getattr(usage, "input_tokens", 0) or 0,
                     "output_tokens": getattr(usage, "output_tokens", 0) or 0}
            usd_val = usd(cfg.get("model", "claude-sonnet-5-5"), **units)
            record_cost(self._k.db, "anthropic", cfg.get("model"), units, usd_val,
                        correlation_id=correlation_id, purpose=purpose, clock=self._k.clock)
        except Exception:
            log.exception("could not record the signup_fieldmap cost")
        return SimpleNamespace(text=text)
