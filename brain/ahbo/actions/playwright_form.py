"""The Playwright form session (§8.3 steps 1–10). One `submit_form(job)` call = one approved action:

connect to the browser container (`settings.browser_ws`; client and server are both Playwright 1.64.1,
`BrowserType.connect(endpoint, *, timeout=…)`) → fresh context (no storage, `accept_downloads=False`) →
open the rewritten URL → extract the visible form controls (one JS pass; accessible names from
`label[for]`) → hard-stop scan (CAPTCHA / login / payment) → map to shareable fields (`field_map`) →
reject forbidden + unknown required → fill (re-reading a `select`'s options right before choosing, so
cascading dropdowns like 活動→時段 work) → `pre_submit.png` → submit → wait for the confirmation →
`post_submit.png` → extract the booking reference.

Hard stops → `needs_human`: CAPTCHA, login form, payment, unknown required field, a cross-domain redirect,
a forbidden field (in `field_map`). Timeouts surface as `failed` (`timeout`): the PRD's §4.3.5 flow maps
error/timeout to FAILED, and the Kernel already runs its own 120 s guard timer for exactly that.

Everything on the page is untrusted data (INV-7): only mapped shareable values are ever typed, and the
confirmation text is only mined for the reference with a strict regex.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from . import Outcome
from . import field_map as fm

log = logging.getLogger("ahbo.actions.playwright_form")

CONFIRM_SELECTOR = "[data-booking-ref], #confirmation, [role=status]"
REF_RE = re.compile(r"\b[A-Z]{1,6}-\d{3,8}\b")

# One evaluate() call returns the visible controls and the page-level hard stops. Field-level forbidden
# detection (HKID / card / password / bank / DOB) is field_map's job and runs on these controls afterwards.
EXTRACT_JS = """
() => {
  const visible = el => {
    if (el.closest('[hidden]') !== null || el.disabled) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 || r.height > 0;
  };
  const controls = Array.from(document.querySelectorAll('input, select, textarea')).filter(el => {
    const t = (el.type || '').toLowerCase();
    return !['hidden', 'submit', 'button', 'image', 'reset'].includes(t) && visible(el);
  });
  const label = el => {
    if (el.id) {
      const l = document.querySelector('label[for="' + window.CSS.escape(el.id) + '"]');
      if (l) return l.textContent.trim();
    }
    const wrap = el.closest('label');
    if (wrap) return wrap.textContent.trim();
    return el.getAttribute('aria-label') || '';
  };
  const out = controls.map(el => {
    const tag = el.tagName.toLowerCase();
    const id = el.id || '';
    const name = el.getAttribute('name') || '';
    const key = id ? '#' + id : (name ? tag + '[name="' + name + '"]' : tag);
    return {
      key: key,
      label: label(el),
      name: name,
      id: id,
      tag: tag,
      type: tag === 'input' ? (el.type || 'text').toLowerCase() : tag,
      required: !!el.required,
      placeholder: el.getAttribute('placeholder') || '',
      autocomplete: el.getAttribute('autocomplete') || '',
      classes: el.className || '',
      options: tag === 'select'
        ? Array.from(el.options).filter(o => o.value).map(o => ({value: o.value, text: o.textContent.trim()}))
        : [],
      value: el.value || ''
    };
  });
  const stops = [];
  const hay = c => [c.label, c.name, c.id, c.placeholder, c.classes].join(' ');
  if (out.some(c => c.type === 'password')) stops.push('login');
  if (out.some(c => /captcha|驗證碼|recaptcha|機械人/i.test(hay(c)))) stops.push('captcha');
  if (out.some(c => /^cc-/.test(c.autocomplete) ||
                   /信用卡|card\\s*number|卡號|cvv|cvc|security\\s*code/i.test(hay(c)))) stops.push('payment');
  return {controls: out, stops: stops};
}
"""


@dataclass
class Job:
    """Everything one browser run needs; built by `runner` from the action row."""

    action_id: str
    url: str                        # the allowlisted PUBLIC url (what the allowlist was checked on)
    navigate_to: str                # what the browser actually opens (internal rewrite, same page)
    shareable: dict[str, Any]
    choice: fm.ActionChoice
    forbidden_terms: list[str]
    shots_dir: Any                  # Path: data/media/actions/<action_id>/
    budget_s: float = 110.0         # stays under the Kernel's 120 s action guard
    llm: fm.FieldMapLLM | None = None   # real mode only (purpose signup_fieldmap); validated in field_map
    notes: list[dict[str, Any]] = field(default_factory=list)    # mapping notes for the detail payload


class _Deadline:
    """The job's whole budget (§8.3 hard stop), shared by every Playwright wait."""

    def __init__(self, seconds: float) -> None:
        self.end = time.monotonic() + seconds

    def ms(self, cap: int = 30000) -> int:
        left = (self.end - time.monotonic()) * 1000
        return int(max(1.0, min(left, cap)))


async def submit_form(job: Job, settings: Any, *, browser_ws: str | None = None) -> Outcome:
    """The §8.3 steps. Playwright is imported lazily so this module imports anywhere."""
    from playwright.async_api import async_playwright

    ws = browser_ws or settings.browser_ws
    pw = await async_playwright().start()
    shots: list[str] = []
    try:
        browser = await pw.chromium.connect(ws, timeout=10000)
        context = await browser.new_context(accept_downloads=False)   # fresh + no storage (§8.3)
        page = await context.new_page()
        try:
            return await _run(job, page, shots, _Deadline(job.budget_s))
        finally:
            for closer in (context.close, browser.close):
                try:
                    await closer()
                except Exception:
                    log.debug("close failed for %s", job.action_id, exc_info=True)
    except Exception as e:            # includes playwright's TimeoutError and connect failures
        reason = "timeout" if _is_timeout(e) else "browser_error"
        if reason != "timeout":
            log.exception("playwright run failed for %s", job.action_id)
        return Outcome("failed", {"reason": reason, "error": repr(e)[:200]}, shots)
    finally:
        try:
            await pw.stop()
        except Exception:
            log.debug("playwright stop failed", exc_info=True)


def _is_timeout(e: Exception) -> bool:
    name = type(e).__name__
    return name in ("TimeoutError", "Timeout") or isinstance(e, TimeoutError)


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower() if url else ""


async def _fields(page: Any) -> list[fm.FormField]:
    scan = await page.evaluate(EXTRACT_JS)
    return [fm.FormField(**c) for c in scan["controls"]]


async def _take(page: Any, job: Job, shots: list[str], name: str) -> str:
    job.shots_dir.mkdir(parents=True, exist_ok=True)
    path = job.shots_dir / f"{name}.png"
    try:
        await page.screenshot(path=str(path), full_page=True, type="png", timeout=15000)
    except Exception:
        log.warning("screenshot %s failed for %s", name, job.action_id)
        return ""
    shots.append(str(path))
    return str(path)


async def _run(job: Job, page: Any, shots: list[str], dl: _Deadline) -> Outcome:
    # The browser opens the REWRITTEN url, so "cross-domain" is measured against where we actually
    # navigated (a public→internal rewrite is by design, not a redirect), then against wherever the
    # site itself landed us — any further host change is a hard stop.
    await page.goto(job.navigate_to, wait_until="load", timeout=dl.ms())
    landed_host = _host(page.url)
    if landed_host != _host(job.navigate_to):
        await _take(page, job, shots, "redirect")
        return Outcome("needs_human", {"reason": "cross_domain_redirect", "landed": page.url}, shots)

    scan = await page.evaluate(EXTRACT_JS)
    if scan["stops"]:
        await _take(page, job, shots, "hard_stop")
        return Outcome("needs_human", {"reason": scan["stops"][0]}, shots)

    mapped = await fm.map_fields(await _fields(page), job.shareable, job.choice, job.forbidden_terms,
                                 job.llm, correlation_id=job.action_id)
    job.notes.append({"mapped": mapped.as_dict()})
    if not mapped.ok:
        await _take(page, job, shots, "blocked")
        return Outcome("needs_human", {"reason": mapped.reason, "forbidden": mapped.forbidden,
                                       "unknown_required": mapped.unknown_required}, shots)

    for f in mapped.fills:
        loc = page.locator(f.key)
        if await loc.count() > 1:
            loc = loc.first
        if f.kind == "fill":
            await loc.fill(f.value, timeout=dl.ms())
            continue
        if f.kind == "select":                     # already resolved against the options seen at map time
            await loc.select_option(f.value, timeout=dl.ms())
        else:                                      # select_deferred: options arrive only after the parent
            fresh = next((x for x in await _fields(page) if x.key == f.key), None)   # select changes
            wanted = ([job.choice.activity_id, job.choice.activity_title] if f.source == "activity"
                      else [job.choice.session_value, job.choice.time])
            v = fm.choose_option(fresh, [w for w in wanted if w]) if fresh is not None else None
            if not v:
                await _take(page, job, shots, "blocked")
                return Outcome("needs_human", {"reason": "unknown_required_field",
                                               "unknown_required": [f.label]}, shots)
            await loc.select_option(v, timeout=dl.ms())
        await page.wait_for_timeout(120)           # let any dependent dropdown repopulate

    pre = await _take(page, job, shots, "pre_submit")
    submit = page.locator("form button[type=submit], form input[type=submit]").first
    if await submit.count() == 0:
        submit = page.locator("form button").first
    await submit.click(timeout=dl.ms())
    try:
        await page.wait_for_selector(CONFIRM_SELECTOR, state="visible", timeout=dl.ms())
    except Exception:
        err = page.locator("[role=alert]").first
        if await err.count() and await err.is_visible():
            await _take(page, job, shots, "rejected")
            return Outcome("failed", {"reason": "submit_rejected",
                                      "message": ((await err.text_content()) or "").strip()[:120]}, shots)
        return Outcome("failed", {"reason": "timeout"}, shots)
    if _host(page.url) != landed_host:
        await _take(page, job, shots, "redirect")
        return Outcome("needs_human", {"reason": "cross_domain_redirect", "landed": page.url}, shots)

    post = await _take(page, job, shots, "post_submit")
    ref = ""
    ref_el = page.locator("[data-booking-ref]").first
    if await ref_el.count():
        ref = ((await ref_el.text_content()) or "").strip()
    if not ref:
        m = REF_RE.search((await page.locator("body").text_content()) or "")
        ref = m.group(0) if m else ""
    return Outcome("done", {"reason": "booked", "reference": ref, "url": page.url,
                            "pre": pre, "post": post, "notes": job.notes}, shots)
