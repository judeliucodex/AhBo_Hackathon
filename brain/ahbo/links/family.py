"""Family Link dispatcher (§9.2): the `family.send` command → Telegram (real or fake).

Command payload (CONTRACTS §4.2): `kind` (ALERT NOTIFY INFO DIGEST HANDOFF), `contact_ids` (contact ids or
names; empty = every `family` contact), `text`, `photo_id?`, `buttons?` (ids from BUTTONS, or
`{"id"|"button": …}` dicts), `screenshots?` (file names under media/screens or absolute paths), `vision_note?`.

Rules:
- Every message is one `outbox` row per contact (fake=1 in fake mode; real rows carry the Telegram message id).
- INFO: at most one per contact per 10 min; the rest are held and merged into one message at the end of the
  window. ALERT (and every other kind) is exempt.
- A failed send is retried with exponential backoff for up to 10 min (§10.4 "Telegram down").
- The result comes back as a `family.send_result` event (source `telegram`), correlated with the command.

`[打俾媽媽]` (call_mum) is replaced by `[錄段聲俾媽媽]` (voice_mum; owner decision 11 Nov 2026): Twilio cannot ring
the iPad (no telephony endpoint, §25.2), so the button tells 阿珍 to send a voice note, which Ah Bo plays to 媽媽
(voice notes already work). The Kernel still asks for `call_mum`; `button_ids` maps it to `voice_mum`, and a stale
`call_mum` press from an old message is answered the same way.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ahbo.core.events import Command, Event
from ahbo.core.ids import new_id

from . import outbox

log = logging.getLogger("ahbo.links.family")

KINDS = ("ALERT", "NOTIFY", "INFO", "DIGEST", "HANDOFF")
BUTTONS = {"handled": "我處理緊", "voice_mum": "錄段聲俾媽媽", "resolved": "已解決", "confirm": "確認資料",
           "call_mum": "錄段聲俾媽媽"}          # call_mum: legacy id (Kernel + old messages) → shown/handled as voice_mum
LEGACY = {"call_mum": "voice_mum"}
DEFAULT_BUTTONS = {"ALERT": ["handled", "voice_mum", "resolved"]}
HEADERS = {"ALERT": "🚨【緊急】", "NOTIFY": "✅【完成】", "INFO": "ℹ️【通知】", "DIGEST": "📋【今日摘要】",
           "HANDOFF": "🤝【交接】"}

INFO_WINDOW_S = 600.0       # ≤ 1 INFO per 10 min per contact (§9.2)
RETRY_WINDOW_S = 600.0      # retry for 10 min (§10.4)
RETRY_FIRST_S = 2.0
RETRY_MAX_S = 60.0


def callback_data(button: str, outbox_id: str) -> str:
    """Telegram callback_data (≤ 64 bytes): `b:<button>:<outbox id>`."""
    return f"b:{button}:{outbox_id}"


def parse_callback(data: str | None) -> tuple[str | None, str | None]:
    parts = (data or "").split(":", 2)
    if len(parts) == 3 and parts[0] == "b" and parts[1] in BUTTONS:
        return parts[1], parts[2] or None
    return None, None


def button_ids(kind: str, raw: list[Any] | None) -> list[str]:
    ids: list[str] = []
    for b in (raw if raw is not None else DEFAULT_BUTTONS.get(kind, [])):
        bid = b if isinstance(b, str) else (b.get("id") or b.get("button") or "")
        bid = LEGACY.get(bid, bid)
        if bid not in BUTTONS:
            log.warning("unknown family button %r ignored", bid)
            continue
        if bid not in ids:
            ids.append(bid)
    return ids


def render_buttons(ids: list[str], outbox_id: str) -> list[dict[str, str]]:
    return [{"id": b, "text": BUTTONS[b], "callback_data": callback_data(b, outbox_id)} for b in ids]


def compose(kind: str, text: str, *, vision_note: str | None = None, hhmm: str | None = None) -> str:
    lines = [HEADERS.get(kind, ""), text.strip()]
    if vision_note:
        lines.append(f"相片判斷（只供參考）：{vision_note}")
    if kind == "ALERT" and hhmm:
        lines.append(f"時間：{hhmm}")
    return "\n".join(x for x in lines if x)


def resolve_contacts(db: Any, ids: list[str] | None) -> list[dict[str, Any]]:
    if not ids:
        return db.query("SELECT * FROM contacts WHERE role='family' ORDER BY priority")
    out = []
    for cid in ids:
        r = db.one("SELECT * FROM contacts WHERE id=? OR name=?", (cid, cid))
        if r is None:
            log.warning("family.send: unknown contact %r", cid)
            continue
        if r not in out:
            out.append(r)
    return out


@dataclass
class _Window:
    last_sent: float = -1e18
    held: list[dict[str, Any]] = field(default_factory=list)
    flush_task: Any = None


class FamilyLink:
    def __init__(self, svc: Any, channel: Any, *, fake: bool):
        self.svc, self.channel, self.fake = svc, channel, fake
        self.kernel = svc.kernel
        self.db = svc.db
        self.windows: dict[str, _Window] = {}
        self.info_window_s = INFO_WINDOW_S
        self.retry_window_s = RETRY_WINDOW_S

    # ---------- helpers ----------
    @property
    def scale(self) -> float:
        return float(getattr(self.kernel, "time_scale", 1.0) or 1.0)

    def _now_iso(self) -> str:
        return self.kernel.clock.iso()

    def _mono(self) -> float:
        return asyncio.get_running_loop().time()

    def _media_dir(self) -> Path:
        return self.kernel.settings.media_dir

    def photo_path(self, photo_id: str | None) -> Path | None:
        if not photo_id:
            return None
        r = self.db.one("SELECT path FROM media WHERE id=?", (photo_id,))
        if r and r.get("path"):
            p = Path(r["path"])
            if not p.is_absolute():
                p = self._media_dir() / p
            if p.exists():
                return p
        for sub in ("incidents", "photos", "photo"):
            for ext in ("jpg", "jpeg", "png"):
                p = self._media_dir() / sub / f"{photo_id}.{ext}"
                if p.exists():
                    return p
        log.warning("photo %s not found; sending without it", photo_id)
        return None

    def screenshot_paths(self, names: list[str] | None) -> list[Path]:
        out = []
        for n in names or []:
            p = Path(n)
            if not p.is_absolute():
                p = self._media_dir() / "screens" / n
            if p.exists():
                out.append(p)
            else:
                log.warning("screenshot %s not found", n)
        return out

    async def _result(self, c: Command | None, corr: str | None, **payload: Any) -> None:
        await self.kernel.submit(Event(type="family.send_result", source="telegram", correlation_id=corr,
                                       payload={"command_id": c.id if c else None, **payload}))

    # ---------- dispatcher ----------
    def dispatch(self, c: Command) -> None:
        """`family.send` dispatcher: records outbox rows now, delivers in the background."""
        p = c.payload
        kind = str(p.get("kind") or "INFO").upper()
        if kind not in KINDS:
            log.warning("family.send: unknown kind %r → INFO", kind)
            kind = "INFO"
        contacts = resolve_contacts(self.db, p.get("contact_ids") or ([p["contact_id"]] if p.get("contact_id")
                                                                       else None))
        if not contacts:
            self.kernel.spawn(self._result(c, c.correlation_id, ok=False, kind=kind, status="failed",
                                           reason="no_contacts"), name="family.result")
            return
        msg = {"kind": kind, "text": str(p.get("text") or ""), "photo_id": p.get("photo_id"),
               "screenshots": list(p.get("screenshots") or []), "vision_note": p.get("vision_note"),
               "buttons": button_ids(kind, p.get("buttons")), "hhmm": self.kernel.clock.now().strftime("%H:%M"),
               "correlation_id": c.correlation_id, "command": c}
        for contact in contacts:
            self._route(contact, msg)

    def _route(self, contact: dict[str, Any], msg: dict[str, Any]) -> None:
        if msg["kind"] != "INFO":
            self._send_now(contact, msg)
            return
        w = self.windows.setdefault(contact["id"], _Window())
        now = self._mono()
        if not w.held and now - w.last_sent >= self.info_window_s * self.scale:
            w.last_sent = now
            self._send_now(contact, msg)
            return
        oid = self._record(contact, msg, status="held")
        w.held.append({**msg, "outbox_id": oid})
        if w.flush_task is None:
            delay = max(0.0, w.last_sent + self.info_window_s * self.scale - now)
            w.flush_task = self.kernel.spawn(self._flush_later(contact, delay), name="family.info_flush")

    async def _flush_later(self, contact: dict[str, Any], delay: float) -> None:
        await asyncio.sleep(delay)
        await self.flush(contact["id"])

    async def flush(self, contact_id: str) -> str | None:
        """Send the held INFO messages for one contact as a single merged message."""
        w = self.windows.get(contact_id)
        if not w or not w.held:
            if w:
                w.flush_task = None
            return None
        held, w.held, w.flush_task = w.held, [], None
        w.last_sent = self._mono()
        contact = self.db.one("SELECT * FROM contacts WHERE id=?", (contact_id,))
        if contact is None:
            return None
        texts = [h["text"].strip() for h in held if h["text"].strip()]
        merged_text = texts[0] if len(texts) == 1 else "\n".join(f"・{t}" for t in texts)
        if len(held) > 1:
            merged_text = f"（{len(held)} 則通知合併）\n{merged_text}"
        btns: list[str] = []
        for h in held:
            btns += [b for b in h["buttons"] if b not in btns]
        last = held[-1]
        merged = {**last, "text": merged_text, "buttons": btns, "photo_id": None, "screenshots": [],
                  "merged_from": [h["outbox_id"] for h in held]}
        oid = self._send_now(contact, merged)
        for h in held:
            outbox.update(self.db, h["outbox_id"], ts=self._now_iso(), status="merged",
                          payload_patch={"merged_into": oid}, kernel=self.kernel)
        return oid

    def _record(self, contact: dict[str, Any], msg: dict[str, Any], status: str) -> str:
        oid = new_id("ob")
        btns = render_buttons(msg["buttons"], oid)
        text = compose(msg["kind"], msg["text"], vision_note=msg.get("vision_note"), hhmm=msg.get("hhmm"))
        chat_id = contact.get("telegram_chat_id")
        payload = {"text": text, "photo_id": msg.get("photo_id"), "screenshots": msg.get("screenshots") or [],
                   "contact_name": contact.get("name"), "command_id": msg["command"].id if msg.get("command") else None}
        if msg.get("merged_from"):
            payload["merged_from"] = msg["merged_from"]
        outbox.record(self.db, ts=self._now_iso(), channel="telegram", kind=msg["kind"], to_contact=contact["id"],
                      to_address=str(chat_id) if chat_id else (f"fake:{contact['id']}" if self.fake else None),
                      payload=payload, buttons=btns, fake=self.fake, status=status,
                      correlation_id=msg.get("correlation_id"), id_=oid, kernel=self.kernel)
        return oid

    def _send_now(self, contact: dict[str, Any], msg: dict[str, Any]) -> str:
        oid = self._record(contact, msg, status="queued")
        self.kernel.spawn(self._deliver(oid, contact, msg), name="family.deliver")
        return oid

    async def _deliver(self, oid: str, contact: dict[str, Any], msg: dict[str, Any]) -> dict[str, Any]:
        row = outbox.get(self.db, oid) or {}
        text = row.get("payload", {}).get("text", msg["text"])
        chat_id = contact.get("telegram_chat_id")
        photo = self.photo_path(msg.get("photo_id"))
        shots = self.screenshot_paths(msg.get("screenshots"))
        buttons = row.get("buttons") or []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.retry_window_s * self.scale
        delay, attempts = RETRY_FIRST_S, 0
        res: dict[str, Any] = {}
        while True:
            attempts += 1
            try:
                res = await self.channel.send(kind=msg["kind"], contact_id=contact["id"],
                                              chat_id=str(chat_id) if chat_id else None, text=text,
                                              photo_path=photo, buttons=[{"text": b["text"],
                                                                          "callback_data": b["callback_data"]}
                                                                         for b in buttons] or None,
                                              screenshots=shots or None, correlation_id=msg.get("correlation_id"))
            except Exception as e:  # never let a channel error escape; it becomes a retry or a failure
                log.warning("family send attempt %d failed: %s", attempts, type(e).__name__)
                res = {"ok": False, "reason": type(e).__name__, "retryable": True}
            if res.get("ok"):
                outbox.update(self.db, oid, ts=self._now_iso(), status="sent",
                              provider_id=str(res["provider_id"]) if res.get("provider_id") is not None else None,
                              payload_patch={"attempts": attempts}, kernel=self.kernel)
                await self._result(msg.get("command"), msg.get("correlation_id"), ok=True, outbox_id=oid,
                                   contact_id=contact["id"], kind=msg["kind"], status="sent", attempts=attempts,
                                   provider_id=res.get("provider_id"), fake=self.fake)
                return res
            wait = max(delay, float(res.get("retry_after") or 0))
            if not res.get("retryable", True) or loop.time() + wait * self.scale > deadline:
                outbox.update(self.db, oid, ts=self._now_iso(), status="failed",
                              payload_patch={"attempts": attempts, "reason": res.get("reason")}, kernel=self.kernel)
                await self._result(msg.get("command"), msg.get("correlation_id"), ok=False, outbox_id=oid,
                                   contact_id=contact["id"], kind=msg["kind"], status="failed", attempts=attempts,
                                   reason=res.get("reason") or "send_failed", fake=self.fake)
                return res
            outbox.update(self.db, oid, ts=self._now_iso(), status="retrying",
                          payload_patch={"attempts": attempts, "reason": res.get("reason")}, kernel=self.kernel)
            await asyncio.sleep(wait * self.scale)
            delay = min(delay * 2, RETRY_MAX_S)
