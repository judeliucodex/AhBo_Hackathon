"""Phone Link over Twilio voice (§9.3, D6) — **safety-critical: the INV-9 dial guard lives here.**

The dispatcher for `phone.call`:
1. `guard_call()` — an independent, second INV-9 guard (the Kernel has its own in `kernel/common.py`): only
   `contacts` rows with `verified=1` and role ∈ {emergency_centre_sim, family}; raw numbers in the payload
   (`to` / `to_e164` / `number` / `phone`) are refused outright; the contact's number, in any formatting
   (full-width digits included), is refused when it is 999 / 112 / 911 — exactly or as a short-code suffix —
   or has ≤ 4 digits, or is unusable. The only exception is fake mode with no number at all (nothing can be
   dialled; the seeded demo contacts carry no number). **A refused call writes no `outbox` row.**
2. Daily cap 5 calls, except safety calls ≤ 3 per incident (`ahbo.ops.caps.check_cap`, imported lazily).
3. Outbox row (status queued, provider_id = our `call_id`), then the channel call in the background; results
   come back as `phone.call_status` events (source `twilio`).

TwiML routes (`POST /twilio/twiml/{call_id}`, `/twilio/status`, `/twilio/gather`) are validated with the SDK's
`RequestValidator` (URL = PUBLIC_BASE_URL + path + query). `GET /twilio/twiml/test` is a cookie-authenticated
dry run. The `<Play>` clip is the P1 clip published at the unguessable `/media/public/{rand}/…` path
(CONTRACTS §8, kv `public_clip_path`).
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import shutil
import unicodedata
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from ahbo.core.events import Command, Event
from ahbo.core.ids import new_id
from ahbo.core.settings import EMERGENCY_NUMBERS

from . import current_svc, outbox

log = logging.getLogger("ahbo.links.twilio")

ALLOWED_ROLES = ("emergency_centre_sim", "family")   # INV-9: nobody else is ever dialled
TFL_VOICE = "zh-HK-HiuMaanNeural"

router = APIRouter()


# ==================================================================== INV-9 guard (small and obvious)
def only_digits(raw: Any) -> str:
    """All digits in `raw`, normalising full-width/other Unicode digits."""
    out = []
    for ch in str(raw or ""):
        if ch.isdigit():
            try:
                out.append(str(unicodedata.digit(ch)))
            except (TypeError, ValueError):
                out.append(ch)
    return "".join(out)


def is_emergency_digits(digits: str) -> bool:
    """999 / 112 / 911 exactly, or as the tail of a short code (≤ 5 extra digits before them)."""
    if not digits:
        return False
    return any(digits == n or (digits.endswith(n) and len(digits) - len(n) <= 5) for n in EMERGENCY_NUMBERS)


def to_e164(raw: Any) -> str | None:
    """E.164; 8-digit numbers with no country code are Hong Kong (+852). None if unusable."""
    s = str(raw or "").strip()
    d = only_digits(s)
    if not d:
        return None
    if s.startswith("+"):
        return "+" + d
    if len(d) == 8:
        return "+852" + d
    if len(d) > 8:
        return "+" + d
    return None


def guard_call(db: Any, payload: dict[str, Any], *, fake: bool) -> tuple[dict[str, Any] | None, str | None, str]:
    """`(contact, to_e164, reason)`; `reason` is None only when the call is allowed.

    Allowed: the payload names a contact id (never a raw number), the contact exists, its role is callable,
    it is verified, and its number has ≥ 8 diallable digits and is not 999/112/911 in any formatting (exact
    or as a short-code suffix). Fake mode also allows a verified contact with no number at all (nothing is
    dialled — the seeded demo contacts carry none).
    """
    if any(payload.get(k) for k in ("to", "to_e164", "number", "phone")):
        return None, None, "no_raw_numbers"
    contact_id = payload.get("contact_id")
    row = db.one("SELECT * FROM contacts WHERE id=?", (contact_id,)) if contact_id else None
    if row is None:
        return None, None, "unknown_contact"
    if row.get("role") not in ALLOWED_ROLES:
        return None, None, "role_not_allowed"
    if int(row.get("verified") or 0) != 1:
        return None, None, "unverified"
    digits = only_digits(row.get("phone"))
    if not digits and fake:
        return row, "", None                          # fake call: nothing to dial, no number configured
    if is_emergency_digits(digits):
        return None, None, "emergency_number"
    e164 = to_e164(row.get("phone"))
    if e164 is None or len(only_digits(e164)) < 8:    # unusable, short codes and partial numbers (< 8 digits)
        return None, None, "short_or_missing_number"  # are never dialled (same set the Kernel refuses)
    return row, e164, None


# ==================================================================== TwiML
def call_twiml(clip_url: str, gather_url: str) -> str:
    """The P1 clip played twice, then `<Gather numDigits="1">`; no input → spoken goodbye, hang up (§9.3)."""
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
            "<Response>"
            f"<Play loop=\"2\">{escape(clip_url)}</Play>"
            f"<Gather numDigits=\"1\" action={quoteattr(gather_url)} method=\"POST\" timeout=\"12\">"
            f"<Say language=\"zh-HK\" voice=\"{TFL_VOICE}\">收到阿寶嘅緊急電話，請撳一個字確認。</Say>"
            "</Gather>"
            f"<Say language=\"zh-HK\" voice=\"{TFL_VOICE}\">收唔到確認，再見。</Say>"
            "</Response>")


def ack_twiml() -> str:
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
            "<Response>"
            f"<Say language=\"zh-HK\" voice=\"{TFL_VOICE}\">多謝確認，我哋即刻跟進。</Say>"
            "<Hangup/>"
            "</Response>")


TWILIO_STATUS_MAP = {"queued": "queued", "ringing": "ringing", "in-progress": "in-progress",
                     "completed": "completed", "busy": "busy", "failed": "failed", "no-answer": "no-answer",
                     "canceled": "failed"}


# ==================================================================== channels
class FakeTwilio:
    """Fake channel (§26.3): the outbox row gets the TwiML; a status sequence runs ringing → in-progress →
    completed over 6 s (× the Kernel's time scale). The console's 「press 1」 makes it acknowledged."""

    def __init__(self, svc: Any):
        self.svc = svc

    async def call(self, *, to_e164: str, contact_id: str, call_id: str, clip_url: str,
                   correlation_id: str | None = None, outbox_id: str | None = None) -> dict[str, Any]:
        k = self.svc.kernel
        if outbox_id:
            base = self.svc.settings.public_base_url
            outbox.update(self.svc.db, outbox_id, ts=k.clock.iso(), status="ringing",
                          payload_patch={"twiml": call_twiml(clip_url, f"{base}/twilio/gather?call_id={call_id}"),
                                         "fake_sequence": True}, kernel=k)
        seq = [("ringing", 0.5), ("in-progress", 1.5), ("completed", 6.0)]
        prev = 0.0

        async def run() -> None:
            nonlocal prev
            for status, at in seq:
                await _sleep_scaled(k, at - prev)
                prev = at
                if outbox_id:
                    outbox.update(self.svc.db, outbox_id, ts=k.clock.iso(), status=status, kernel=k)
                await k.submit(status_event(call_id, status, correlation_id, outbox_id))
        k.spawn(run(), name="fake_call_status")
        return {"ok": True, "provider_id": call_id, "fake": True}


class TwilioReal:
    """The `twilio` SDK. `calls.create` runs in a thread (the SDK is blocking). The TwiML URL carries our own
    `call_id`; Twilio's call SID is stored in the row's payload (`call_sid`)."""

    def __init__(self, svc: Any):
        self.svc = svc
        s = svc.settings
        if not (s.twilio_account_sid and s.twilio_auth_token and s.twilio_from):
            raise RuntimeError("twilio keys missing (TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM)")

    async def call(self, *, to_e164: str, contact_id: str, call_id: str, clip_url: str,
                   correlation_id: str | None = None, outbox_id: str | None = None) -> dict[str, Any]:
        from twilio.rest import Client
        s = self.svc.settings
        base = s.public_base_url
        client = Client(s.twilio_account_sid, s.twilio_auth_token)
        try:
            call = await asyncio.to_thread(
                lambda: client.calls.create(
                    to=to_e164, from_=s.twilio_from,
                    url=f"{base}/twilio/twiml/{call_id}", method="POST",
                    status_callback=f"{base}/twilio/status?call_id={call_id}",
                    status_callback_event=["ringing", "in-progress", "completed", "no-answer", "busy", "failed",
                                           "canceled"],
                    status_callback_method="POST", timeout=30))
        except Exception as e:
            log.warning("twilio call create failed: %r", e)
            if outbox_id:
                outbox.update(self.svc.db, outbox_id, ts=self.svc.kernel.clock.iso(), status="failed",
                              payload_patch={"reason": type(e).__name__}, kernel=self.svc.kernel)
            await self.svc.kernel.submit(status_event(call_id, "failed", correlation_id, outbox_id))
            return {"ok": False, "reason": type(e).__name__}
        if outbox_id:
            outbox.update(self.svc.db, outbox_id, ts=self.svc.kernel.clock.iso(), status="ringing",
                          payload_patch={"call_sid": call.sid}, kernel=self.svc.kernel)
        return {"ok": True, "provider_id": call.sid}


def status_event(call_id: str, status: str, correlation_id: str | None, outbox_id: str | None = None) -> Event:
    payload: dict[str, Any] = {"call_id": call_id, "status": status}
    if correlation_id:
        payload["incident_id"] = correlation_id
    if outbox_id:
        payload["outbox_id"] = outbox_id
    return Event(type="phone.call_status", source="twilio", correlation_id=correlation_id, payload=payload)


async def _sleep_scaled(kernel: Any, seconds: float) -> None:
    await asyncio.sleep(max(0.0, seconds) * float(getattr(kernel, "time_scale", 1.0) or 1.0))


# ==================================================================== dispatcher (`phone.call` command)
def phone_dispatcher(svc: Any, channel: Any, *, fake: bool):
    """Guard (INV-9) → cap → outbox row → background call. Refused calls never write an outbox row."""

    def dispatch(c: Command) -> None:
        svc.kernel.spawn(handle(svc, channel, c, fake=fake), name="phone_call")

    return dispatch


async def handle(svc: Any, channel: Any, c: Command, *, fake: bool) -> dict[str, Any] | None:
    k, db = svc.kernel, svc.db
    p = c.payload
    contact, e164, reason = guard_call(db, p, fake=fake)
    if contact is None:
        _refuse(k, c, reason or "refused", None)
        return None
    incident_id = p.get("incident_id") or c.correlation_id
    ok_cap, used, cap = _cap_ok(svc, bool(incident_id), incident_id)
    if not ok_cap:
        _refuse(k, c, "cap_reached", f"twilio_calls used={used} cap={cap}")
        return None
    call_id = str(p.get("call_id") or new_id("call"))
    clip_url = public_clip_url(svc)
    oid = outbox.record(db, ts=k.clock.iso(), channel="twilio", kind="CALL", to_contact=contact["id"],
                        to_address=e164 or None,
                        payload={"call_id": call_id, "clip": p.get("clip"), "incident_id": incident_id,
                                 "contact_name": contact.get("name")},
                        fake=fake, status="queued", correlation_id=incident_id, provider_id=call_id, kernel=k)
    _record_usage(svc, incident_id if isinstance(incident_id, str) else None)
    return await channel.call(to_e164=e164, contact_id=contact["id"], call_id=call_id, clip_url=clip_url,
                              correlation_id=incident_id, outbox_id=oid)


def _record_usage(svc: Any, incident_id: str | None) -> None:
    """Count the attempt against the daily cap (`check_cap` reads this back; §11)."""
    try:
        from ahbo.trace.costs import record_cost
        record_cost(svc.db, "twilio", None, {"calls": 1}, 0.0, correlation_id=incident_id,
                    purpose="safety_call" if incident_id else "phone_call", clock=svc.kernel.clock)
    except Exception:
        log.exception("recording twilio usage failed")


def _refuse(k: Any, c: Command, reason: str, detail: str | None) -> None:
    """Refusals are traced (INV-6) but never dialled and never written to the outbox."""
    log.warning("INV-9: phone.call refused (%s%s)", reason, f"; {detail}" if detail else "")
    k.note(Event(type="phone.call_refused", source="twilio", correlation_id=c.correlation_id,
                 payload={"reason": reason, "command_id": c.id,
                          "incident_id": c.payload.get("incident_id")}))


def _cap_ok(svc: Any, safety: bool, incident_id: str | None) -> tuple[bool, float, float]:
    try:                                   # lazy import (ops may not be wired in every environment)
        from ahbo.ops.caps import check_cap
    except Exception:
        return True, 0.0, 0.0
    return check_cap(svc.db, svc.kernel.clock, svc.config, "twilio_calls", safety=safety, incident_id=incident_id)


# ==================================================================== the public P1 clip (CONTRACTS §8)
def public_clip_url(svc: Any) -> str:
    db = svc.db
    rand = db.kv_get("public_clip_rand")
    path = db.kv_get("public_clip_path")
    if not (rand and path):
        rand = secrets.token_hex(24)
        path = f"/media/public/{rand}/P1.m4a"
        db.kv_set("public_clip_rand", rand)
        db.kv_set("public_clip_path", path)
    _ensure_clip_file(svc, rand)
    return svc.settings.public_base_url + path


def _ensure_clip_file(svc: Any, rand: str) -> None:
    """Copy the rendered P1 clip into the unguessable public dir; if it was never rendered, a silent stand-in
    of the right length (fake-TTS timing, 0.19 s/char) so real Twilio still plays something."""
    d = svc.settings.media_dir / "public" / rand
    target = d / "P1.m4a"
    if target.is_file():
        return
    d.mkdir(parents=True, exist_ok=True)
    src = _rendered_p1(svc)
    if src is not None:
        try:
            shutil.copyfile(src, target)
            return
        except OSError:
            pass
    from .telegram import make_silent_m4a
    make_silent_m4a(target, max(1.0, len(p1_text(svc)) * 0.19))


def _rendered_p1(svc: Any) -> Path | None:
    try:
        from ..api.services import fixed_dir, read_manifest
        manifest = read_manifest(svc)
        f = ((manifest.get("clips") or {}).get("P1") or {}).get("file")
        if f:
            p = Path(fixed_dir(svc)) / str(f)
            if p.is_file():
                return p
    except Exception:
        log.exception("looking up the rendered P1 clip failed")
    return None


def p1_text(svc: Any) -> str:
    tpl = svc.config.fixed_lines.get("P1_TEMPLATE") or "你好，呢度係阿寶。王婆婆喺屋企懷疑跌倒，冇回應。請即刻跟進。"
    try:
        from ..kernel.common import address
        return tpl.replace("{address}", address(svc.kernel.ctx()))
    except Exception:
        return tpl.replace("{address}", "")


# ==================================================================== routes
def valid_signature(svc: Any, request: Request, params: dict[str, str]) -> bool:
    token = svc.settings.twilio_auth_token
    sig = request.headers.get("x-twilio-signature", "")
    if not token or not sig:
        return False
    from twilio.request_validator import RequestValidator
    url = svc.settings.public_base_url + request.url.path + (f"?{request.url.query}" if request.url.query else "")
    try:
        return bool(RequestValidator(str(token)).validate(url, dict(params), str(sig)))
    except Exception:
        log.exception("twilio signature validation failed")
        return False


async def _form(request: Request) -> dict[str, str]:
    try:
        form = await request.form()
    except Exception:
        return {}
    return {str(k): str(v) for k, v in form.items()}


def _row_by_call_id(svc: Any, call_id: str | None) -> dict[str, Any] | None:
    if not call_id:
        return None
    return svc.db.one("SELECT * FROM outbox WHERE channel='twilio' AND provider_id=? ORDER BY rowid DESC LIMIT 1",
                      (call_id,))


@router.post("/twilio/twiml/{call_id}")
async def twiml(call_id: str, request: Request) -> Response:
    svc = current_svc(request)
    params = await _form(request)
    if not valid_signature(svc, request, params):
        return JSONResponse({"ok": False, "reason": "bad_signature"}, status_code=403)
    base = svc.settings.public_base_url
    xml = call_twiml(public_clip_url(svc), f"{base}/twilio/gather?call_id={escape(call_id)}")
    return Response(xml, media_type="application/xml")


@router.post("/twilio/gather")
async def gather(request: Request) -> Response:
    svc = current_svc(request)
    params = await _form(request)
    if not valid_signature(svc, request, params):
        return JSONResponse({"ok": False, "reason": "bad_signature"}, status_code=403)
    call_id = request.query_params.get("call_id", "")
    if params.get("Digits", "") == "1":
        await record_status(svc, call_id, "acknowledged", params.get("CallSid"))
        return Response(ack_twiml(), media_type="application/xml")
    xml = ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
           f"<Response><Redirect method=\"POST\">{escape(svc.settings.public_base_url + '/twilio/twiml/' + call_id)}"
           "</Redirect></Response>")
    return Response(xml, media_type="application/xml")


@router.post("/twilio/status")
async def twilio_status(request: Request) -> Response:
    svc = current_svc(request)
    params = await _form(request)
    if not valid_signature(svc, request, params):
        return JSONResponse({"ok": False, "reason": "bad_signature"}, status_code=403)
    mapped = TWILIO_STATUS_MAP.get(str(params.get("CallStatus") or ""))
    if mapped is None:
        return Response("<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response/>", media_type="application/xml")
    await record_status(svc, request.query_params.get("call_id", ""), mapped, params.get("CallSid"))
    return Response("<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response/>", media_type="application/xml")


async def record_status(svc: Any, call_id: str, status: str, call_sid: str | None = None) -> None:
    """One `phone.call_status` event (and the outbox row's status) for a callback or the console's press-1."""
    row = _row_by_call_id(svc, call_id)
    incident_id = (row or {}).get("correlation_id")
    oid = (row or {}).get("id")
    if row is not None:
        outbox.update(svc.db, oid, ts=svc.kernel.clock.iso(), status=status, kernel=svc.kernel)
    await svc.kernel.submit(status_event(call_id or "", status, incident_id, oid))


try:
    from ..api.auth import require_operator
except Exception:                                    # pragma: no cover (auth always exists in the real app)
    require_operator = None


@router.get("/twilio/twiml/test")
async def twiml_test(request: Request) -> Response:
    """Dry run for the owner (§27.1 row 7): the exact TwiML a call would play. Cookie-authenticated; no call."""
    svc = current_svc(request)
    if require_operator is not None:
        require_operator(request)                    # raises 401 without the operator cookie
    base = svc.settings.public_base_url
    xml = call_twiml(public_clip_url(svc), f"{base}/twilio/gather?call_id=test")
    return Response(xml, media_type="application/xml")
