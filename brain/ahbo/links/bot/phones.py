"""Phone numbers entered from Telegram (INV-9, safety-critical).

- `normalise(raw)` → `(e164, None)` or `(None, reason)`. HK 8-digit numbers get +852; `00` international prefix
  becomes `+`. Refused: empty, 999/112/911 in any formatting (full-width, spaces, +852 999 …; judged on the E.164
  digits exactly like the dial guard), short codes and partial numbers (< 8 digits), HK numbers starting 0/1, bad
  lengths, and anything `links.twilio.guard_call` would refuse (the real guard is run on the result).
- `mask(e164)` shows only the last 4 digits.
- `twilio_verified(svc, e164)` — a free read of Twilio's Outgoing Caller IDs (trial accounts can only call
  verified numbers). Only called when Twilio is real; tests inject `svc.extra["twilio_http_transport"]`
  (an `httpx.MockTransport`). Returns True / False, or None when the lookup failed.
"""
from __future__ import annotations

import logging
from typing import Any

from ahbo.kernel.common import is_emergency_number

from ..twilio import guard_call, is_emergency_digits, only_digits, to_e164

log = logging.getLogger("ahbo.links.bot.phones")

REASONS = {
    "empty": "冇電話號碼。",
    "emergency_number": "唔可以用緊急號碼（999／112／911，或者用佢哋做尾嘅短號碼）。阿寶永遠唔會打呢啲號碼。",
    "short_code": "號碼太短（短號碼唔會打）。請輸入完整號碼，例如 9123 4567 或 +852 9123 4567。",
    "bad_format": "號碼格式唔啱。香港號碼係 8 個字，海外號碼請加 + 同國家碼。",
    "guard": "呢個號碼阿寶唔可以打（安全規則）。",
}


class _OneRow:
    """A one-row stand-in for the DB so the real `guard_call` can judge a number before it is stored."""

    def __init__(self, row: dict[str, Any]):
        self.row = row

    def one(self, sql: str, params: Any = ()) -> dict[str, Any] | None:
        return self.row


def normalise(raw: Any) -> tuple[str | None, str | None]:
    s = str(raw or "").strip()
    digits = only_digits(s)
    if not digits:
        return None, "empty"
    if len(digits) < 8:
        return None, "emergency_number" if is_emergency_digits(digits) else "short_code"
    if s.startswith("00"):
        s, digits = "+" + digits[2:], digits[2:]
    elif s.startswith("＋"):
        s = "+" + s[1:]
    e164 = to_e164(("+" if s.startswith("+") else "") + digits)
    if e164 is None:
        return None, "bad_format"
    d = only_digits(e164)
    if is_emergency_digits(d) or is_emergency_number(e164):
        return None, "emergency_number"
    if not 8 <= len(d) <= 15 or d.startswith("0"):
        return None, "bad_format"
    if d.startswith("852") and (len(d) != 11 or d[3] in "01"):
        return None, "bad_format"              # HK: 8 digits; 0/1 start service and short codes, never dialled
    if is_emergency_digits(d) or is_emergency_number(e164):
        return None, "emergency_number"
    _row, _to, reason = guard_call(_OneRow({"id": "x", "role": "family", "verified": 1, "phone": e164}),
                                   {"contact_id": "x"}, fake=False)
    if reason is not None or _to != e164:
        return None, "guard"
    return e164, None


def reason_text(reason: str | None) -> str:
    return REASONS.get(reason or "", "號碼唔啱。")


def mask(phone: Any) -> str:
    d = only_digits(phone)
    if not d:
        return "（未有）"
    return "••••" + d[-4:]


async def twilio_verified(svc: Any, e164: str) -> bool | None:
    """Is `e164` one of the account's Outgoing Caller IDs? (GET only; no call, no cost.)"""
    s = svc.settings
    sid, tok = s.twilio_account_sid, s.twilio_auth_token
    if not (sid and tok):
        return None
    import httpx
    transport = (getattr(svc, "extra", None) or {}).get("twilio_http_transport")
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/OutgoingCallerIds.json"
    try:
        async with httpx.AsyncClient(timeout=10, auth=(sid, tok), transport=transport) as c:
            r = await c.get(url, params={"PhoneNumber": e164})
            if r.status_code != 200:
                log.warning("twilio caller-id lookup: http %s", r.status_code)
                return None
            ids = r.json().get("outgoing_caller_ids") or []
    except Exception as e:
        log.warning("twilio caller-id lookup failed: %s", type(e).__name__)
        return None
    return any(str(x.get("phone_number") or "") == e164 for x in ids if isinstance(x, dict))
