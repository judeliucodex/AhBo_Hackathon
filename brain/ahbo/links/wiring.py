"""Links wiring (AGENT_BRIEF convention): `install(svc)` registers the `family.send` and `phone.call`
dispatchers (real or fake channels chosen by `settings.is_fake("telegram"|"twilio")`, §26.3) and seeds the
`beacons` table; `router` combines the Telegram webhook, the Twilio callbacks and the sensor ingest route.

The console's fake controls (§26.3) call the helpers here — names/signatures matched to `ahbo.api.demo`:
- `outbox_list(db)` → the Outbox panel (`ahbo.links.outbox.outbox_list`, re-exported);
- `press(svc, outbox_id, button)` → press an outbox row's button: `handled/resolved/voice_mum/confirm` submit
  `family.button` (source `telegram`) with `via:"operator_fake"`, so the trace stays honest about who pressed
  (INV-8); `confirm` also activates the row's pending facts; `press1` on a call row submits
  `phone.call_status {status:"acknowledged"}`;
- `inject_voice_note(svc, file=None)` → a fake inbound voice note from 阿珍 (`seed/voice/demo_daughter.m4a`;
  a silent placeholder is made with ffmpeg if the seed file is missing);
- `inject_text(svc, text)` → a fake inbound text from 阿珍.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from ahbo.core.events import Event
from ahbo.core.ids import new_id

from . import _registry, family, outbox, sensors, telegram, twilio
from .family import FamilyLink
from .outbox import outbox_list  # re-exported for the console (ahbo.api.demo)

log = logging.getLogger("ahbo.links.wiring")

router = APIRouter()
router.include_router(telegram.router)
router.include_router(twilio.router)
router.include_router(sensors.router)


def build_channels(svc: Any) -> tuple[Any, Any]:
    tg = telegram.FakeTelegram(svc) if svc.settings.is_fake("telegram") else telegram.TelegramReal(svc)
    tfl = twilio.FakeTwilio(svc) if svc.settings.is_fake("twilio") else twilio.TwilioReal(svc)
    return tg, tfl


def install(svc: Any) -> None:
    tg, tfl = build_channels(svc)
    link = FamilyLink(svc, tg, fake=svc.settings.is_fake("telegram"))
    svc.kernel.on("family.send", link.dispatch)
    svc.kernel.on("phone.call", twilio.phone_dispatcher(svc, tfl, fake=svc.settings.is_fake("twilio")))
    sensors.install_beacons(svc)
    if not hasattr(svc, "extra"):        # the evals' svc is a SimpleNamespace without the Services fields
        svc.extra = {}
    svc.extra["family_link"] = link
    svc.extra["telegram_channel"] = tg
    svc.extra["twilio_channel"] = tfl
    _registry["svc"] = svc               # route handlers work even without app.state.svc (tests, evals)

    def _reset(_svc: Any) -> None:     # Reset demo reloads `resident`; keep the caregiver's quiet hours there
        from .bot.changes import sync_resident_quiet
        sync_resident_quiet(_svc)
    if hasattr(svc, "on_reset") and _reset not in svc.on_reset:
        svc.on_reset.append(_reset)

    async def _stop(_svc: Any) -> None:
        ch = _svc.extra.get("telegram_channel")
        if ch is not None and hasattr(ch, "aclose"):
            await ch.aclose()
    if hasattr(svc, "on_stop") and _stop not in svc.on_stop:
        svc.on_stop.append(_stop)


# ==================================================================== console helpers (§26.3)
def _family_contact(svc: Any) -> dict[str, Any] | None:
    return svc.db.one("SELECT * FROM contacts WHERE role='family' ORDER BY priority, id LIMIT 1")


async def press(svc: Any, outbox_id: str, button: str) -> dict[str, Any]:
    """The console pressed a button on an outbox row (fake delivery, §26.3)."""
    row = outbox.get(svc.db, outbox_id)
    if row is None:
        return {"ok": False, "reason": "not_found"}
    button = str(button or "").strip()
    now = svc.kernel.clock.iso()
    if row["channel"] == "twilio":
        if button != "press1":
            return {"ok": False, "reason": "bad_button"}
        await twilio.record_status(svc, str(row.get("provider_id") or outbox_id), "acknowledged")
        outbox.update(svc.db, outbox_id, ts=now, payload_patch={"pressed": button, "via": "operator_fake"},
                      kernel=svc.kernel)
        return {"ok": True, "button": button, "via": "operator_fake"}
    if button not in family.BUTTONS:
        return {"ok": False, "reason": "bad_button"}
    button = family.LEGACY.get(button, button)
    payload: dict[str, Any] = {"contact_id": row.get("to_contact"), "button": button,
                               "callback_data": family.callback_data(button, outbox_id),
                               "outbox_id": outbox_id, "via": "operator_fake"}
    confirmed: list[str] = []
    if button == "confirm":
        confirmed = telegram._confirm_facts(svc, row)
        payload["confirmed_fact_ids"] = confirmed
    await svc.kernel.process(Event(type="family.button", source="telegram",
                                   correlation_id=row.get("correlation_id"), payload=payload))
    outbox.update(svc.db, outbox_id, ts=now, payload_patch={"pressed": button, "via": "operator_fake"},
                  kernel=svc.kernel)
    res = {"ok": True, "button": button, "via": "operator_fake", "confirmed": confirmed}
    if button == "voice_mum":
        res["note"] = telegram.VOICE_MUM_REPLY + "（冇撥號）"
    return res


async def inject_text(svc: Any, text: str) -> dict[str, Any]:
    """A fake inbound text from 阿珍 (§26.3): the same `family.text` event the webhook would produce."""
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "reason": "bad_args"}
    contact = _family_contact(svc)
    if contact is None:
        return {"ok": False, "reason": "no_family_contact"}
    await svc.kernel.process(Event(type="family.text", source="telegram",
                                   payload={"contact_id": contact["id"], "text": text[:600],
                                            "via": "operator_fake"}))
    return {"ok": True, "contact_id": contact["id"], "via": "operator_fake"}


async def inject_voice_note(svc: Any, file: str | Path | None = None) -> dict[str, Any]:
    """A fake inbound voice note from 阿珍: a `voice_messages` row + `family.voice_note`, like the webhook."""
    contact = _family_contact(svc)
    if contact is None:
        return {"ok": False, "reason": "no_family_contact"}
    src = Path(file) if file else telegram.ensure_seed_voice()
    if not src.is_file():
        return {"ok": False, "reason": "voice_file_missing"}
    vm_id = new_id("vm")
    dest = svc.settings.media_dir / "voice_in" / f"{vm_id}.m4a"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(src, dest)
    except OSError as e:
        log.warning("could not copy voice note: %r", e)
        return {"ok": False, "reason": "copy_failed"}
    duration = telegram.ffprobe_duration(dest) or 4.0
    transcript = None
    if not file:                      # the seed note's words are known → caption 「阿珍：…」 (QA 25 D7)
        try:
            from ahbo.core.config import load_seed
            transcript = (load_seed("demo.yaml").get("voice_messages") or [{}])[0].get("transcript")
        except Exception:
            transcript = None
    svc.db.execute(
        "INSERT INTO voice_messages(id, direction, from_name, to_name, contact_id, file_path, mime, duration_s, "
        "transcript, received_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (vm_id, "in", contact["name"], "王婆婆", contact["id"], str(dest), "audio/mp4", duration, transcript,
         svc.clock.iso()))
    await svc.kernel.process(Event(type="family.voice_note", source="telegram",
                                   payload={"contact_id": contact["id"], "voice_id": vm_id,
                                            "via": "operator_fake"}))
    return {"ok": True, "voice_id": vm_id, "via": "operator_fake"}
