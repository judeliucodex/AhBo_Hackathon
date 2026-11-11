"""The outbox (§26.3): every outbound Telegram message and Twilio call is a row here.

Fake mode: the row *is* the delivery (the console's Outbox panel renders it, with buttons it can press).
Real mode: the row records what was sent (`fake=0`) and the provider's id (Telegram message id, Twilio call SID).
Every change is pushed to the operator stream as an `outbox` notification (CONTRACTS §9).
"""
from __future__ import annotations

import logging
from typing import Any

from ahbo.core.db import Database
from ahbo.core.ids import new_id
from ahbo.core.jsonutil import dumps, loads

log = logging.getLogger("ahbo.links.outbox")

CHANNELS = ("telegram", "twilio")


def _row(r: dict[str, Any] | None) -> dict[str, Any] | None:
    if r is None:
        return None
    r = dict(r)
    r["payload"] = loads(r.get("payload"), {})
    r["buttons"] = loads(r.get("buttons"), [])
    r["fake"] = bool(r.get("fake"))
    return r


def notify(kernel: Any, row: dict[str, Any] | None) -> None:
    """Push an outbox row to the operator stream (SSE `outbox`)."""
    if kernel is None or row is None:
        return
    fn = getattr(kernel, "_notify", None)
    if fn:
        try:
            fn("outbox", row)
        except Exception:  # the stream must never break delivery
            log.exception("outbox notify failed")


def record(db: Database, *, ts: str, channel: str, kind: str, to_contact: str | None, to_address: str | None,
           payload: dict[str, Any], buttons: list[dict[str, str]] | None = None, fake: bool, status: str = "queued",
           correlation_id: str | None = None, provider_id: str | None = None, id_: str | None = None,
           kernel: Any = None) -> str:
    assert channel in CHANNELS, channel
    oid = id_ or new_id("ob")
    db.execute(
        "INSERT INTO outbox(id, ts, channel, kind, to_contact, to_address, payload, buttons, status, fake, "
        "correlation_id, provider_id, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (oid, ts, channel, kind, to_contact, to_address, dumps(payload), dumps(buttons or []), status, int(fake),
         correlation_id, provider_id, ts))
    notify(kernel, get(db, oid))
    return oid


def update(db: Database, oid: str, *, ts: str, status: str | None = None, provider_id: str | None = None,
           payload_patch: dict[str, Any] | None = None, kernel: Any = None) -> dict[str, Any] | None:
    row = get(db, oid)
    if row is None:
        return None
    payload = row["payload"]
    if payload_patch:
        payload.update(payload_patch)
    db.execute("UPDATE outbox SET status=COALESCE(?, status), provider_id=COALESCE(?, provider_id), payload=?, "
               "updated_at=? WHERE id=?", (status, provider_id, dumps(payload), ts, oid))
    row = get(db, oid)
    notify(kernel, row)
    return row


def get(db: Database, oid: str) -> dict[str, Any] | None:
    return _row(db.one("SELECT * FROM outbox WHERE id=?", (oid,)))


def find_by_provider_id(db: Database, channel: str, provider_id: str) -> dict[str, Any] | None:
    return _row(db.one("SELECT * FROM outbox WHERE channel=? AND provider_id=? ORDER BY ts DESC LIMIT 1",
                       (channel, provider_id)))


def outbox_list(db: Database, limit: int = 100, channel: str | None = None) -> list[dict[str, Any]]:
    """Newest first (operator API `GET /operator/api/outbox`)."""
    if channel:
        rows = db.query("SELECT * FROM outbox WHERE channel=? ORDER BY ts DESC, id DESC LIMIT ?", (channel, limit))
    else:
        rows = db.query("SELECT * FROM outbox ORDER BY ts DESC, id DESC LIMIT ?", (limit,))
    return [_row(r) for r in rows]  # type: ignore[misc]
