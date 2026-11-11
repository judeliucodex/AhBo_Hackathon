"""Family Link over Telegram (§9.2, D7): the real and the fake channel plus the webhook handler.

Real channel: the raw Bot API over `httpx` (no extra SDK). Fake channel: delivery is the `outbox` row the
dispatcher already wrote (§26.3), so `send` only confirms it.

Inbound (`POST /telegram/webhook`, this module's router):
- the `X-Telegram-Bot-Api-Secret-Token` header is checked with `hmac.compare_digest` (wrong/missing → 401/403);
- **allowlist by chat id** (`contacts.telegram_chat_id`): anyone else gets exactly one fixed reply
  「呢個係私人家庭機械人。」 and is dropped — never forwarded to the Kernel/agent (INV-7), except `/whoami`
  (anyone; the reply carries their chat id only);
- text → `family.text` (the Kernel quotes it, never an instruction); voice notes → OGG download → ffmpeg AAC
  → `voice_messages` row → `family.voice_note`; callback buttons → `family.button` (+ `answerCallbackQuery`;
  `confirm` confirms pending facts); commands `/start /status /meds /fact /story`.
`set_webhook()` builds the setWebhook call for the owner's setup — **nothing here ever calls it automatically**.
"""
from __future__ import annotations

import hmac
import logging
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ahbo.core.db import Database
from ahbo.core.ids import new_id
from ahbo.core.jsonutil import dumps, loads
from ahbo.core.settings import SEED_DIR, Settings

from . import current_svc, outbox
from .family import parse_callback

log = logging.getLogger("ahbo.links.telegram")

STOCK_REPLY = "呢個係私人家庭機械人。"
SEED_VOICE = SEED_DIR / "voice" / "demo_daughter.m4a"

router = APIRouter()


# ==================================================================== channels
class FakeTelegram:
    """Fake channel (§26.3): the outbox row *is* the delivery; `send` just confirms it deterministically."""

    def __init__(self, svc: Any):
        self.svc = svc
        self.sent_count = 0

    async def send(self, *, kind: str, contact_id: str, chat_id: str | None, text: str,
                   photo_path: Path | None = None, buttons: list[dict[str, str]] | None = None,
                   screenshots: list[Path] | None = None, correlation_id: str | None = None,
                   outbox_id: str | None = None) -> dict[str, Any]:
        self.sent_count += 1
        return {"ok": True, "provider_id": f"tgfake{self.sent_count:04d}", "fake": True}

    async def download(self, file_id: str) -> bytes:
        raise RuntimeError("no bot in fake mode")

    async def answer_callback(self, callback_query_id: str, text: str | None = None) -> None:
        return None

    async def reply(self, chat_id: str, text: str) -> None:
        log.info("fake telegram reply to %s: %s", chat_id, text)


class TelegramError(RuntimeError):
    def __init__(self, reason: str, retryable: bool = True):
        super().__init__(reason)
        self.reason, self.retryable = reason, retryable


class TelegramReal:
    """The Bot API over httpx. Every error becomes a `TelegramError` the dispatcher retries with backoff."""

    def __init__(self, svc: Any):
        self.svc = svc
        s: Settings = svc.settings
        if not s.telegram_bot_token:
            raise TelegramError("no_bot_token", retryable=False)
        self._base = f"https://api.telegram.org/bot{s.telegram_bot_token}"
        import httpx
        self._http = httpx.AsyncClient(timeout=20.0)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _api(self, method: str, *, data: dict[str, Any] | None = None,
                   files: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            r = await self._http.post(f"{self._base}/{method}",
                                      json=data if files is None else None,
                                      data=data if files is not None else None, files=files)
        except Exception as e:
            raise TelegramError(type(e).__name__) from e
        try:
            body = r.json()
        except ValueError:
            raise TelegramError(f"http_{r.status_code}") from None
        if not body.get("ok"):
            raise TelegramError(str(body.get("description") or "api_error"), retryable=r.status_code >= 500)
        return body.get("result") or {}

    async def send(self, *, kind: str, contact_id: str, chat_id: str | None, text: str,
                   photo_path: Path | None = None, buttons: list[dict[str, str]] | None = None,
                   screenshots: list[Path] | None = None, correlation_id: str | None = None,
                   outbox_id: str | None = None) -> dict[str, Any]:
        if not chat_id:
            return {"ok": False, "reason": "no_chat_id", "retryable": False}
        markup = {"inline_keyboard": [[{"text": b["text"], "callback_data": b["callback_data"]} for b in buttons]]} \
            if buttons else None
        provider_id: Any = None
        if photo_path is not None and Path(photo_path).is_file():
            with open(photo_path, "rb") as f:
                res = await self._api("sendPhoto", data={"chat_id": chat_id, "caption": text[:1024],
                                                         "reply_markup": dumps(markup)} if markup else
                                       {"chat_id": chat_id, "caption": text[:1024]},
                                       files={"photo": (Path(photo_path).name, f)})
            provider_id = res.get("message_id")
        else:
            res = await self._api("sendMessage", data={"chat_id": chat_id, "text": text,
                                                       "reply_markup": dumps(markup) if markup else None})
            provider_id = res.get("message_id")
        for shot in screenshots or []:
            p = Path(shot)
            if not p.is_file():
                continue
            with open(p, "rb") as f:
                res2 = await self._api("sendPhoto", data={"chat_id": chat_id},
                                       files={"photo": (p.name, f)})
            provider_id = provider_id or res2.get("message_id")
        return {"ok": True, "provider_id": provider_id}

    async def download(self, file_id: str) -> bytes:
        res = await self._api("getFile", data={"file_id": file_id})
        file_path = res.get("file_path")
        if not file_path:
            raise TelegramError("no_file_path")
        try:
            r = await self._http.get(f"{self._base}/file/{file_path}")
            r.raise_for_status()
        except Exception as e:
            raise TelegramError(type(e).__name__) from e
        return r.content

    async def answer_callback(self, callback_query_id: str, text: str | None = None) -> None:
        try:
            await self._api("answerCallbackQuery", data={"callback_query_id": callback_query_id,
                                                         "text": text or "收到"})
        except TelegramError as e:
            log.warning("answerCallbackQuery failed: %s", e.reason)

    async def reply(self, chat_id: str, text: str) -> None:
        if not chat_id:
            return
        try:
            await self._api("sendMessage", data={"chat_id": chat_id, "text": text})
        except TelegramError as e:
            log.warning("telegram reply to %s failed: %s", chat_id, e.reason)


# ==================================================================== webhook handling
def allowlisted_contact(db: Database, chat_id: str | None) -> dict[str, Any] | None:
    if not chat_id:
        return None
    return db.one("SELECT * FROM contacts WHERE telegram_chat_id=? AND role='family'", (str(chat_id),))


def _clip(v: Any, limit: int) -> str:
    return " ".join(str(v or "").split())[:limit]


async def handle_update(svc: Any, update: dict[str, Any]) -> str | None:
    """One Telegram update → Kernel events / DB rows. Returns the reply text (also sent when the channel is real).

    Returns None for updates that are dropped (strangers, unknown payloads). Safe: never raises to the caller.
    """
    try:
        return await _handle(svc, update)
    except Exception:
        log.exception("telegram update handling failed")
        return None


async def _handle(svc: Any, update: dict[str, Any]) -> str | None:
    if not isinstance(update, dict):
        return None
    cb = update.get("callback_query")
    if isinstance(cb, dict):
        return await _on_callback(svc, cb)
    msg = update.get("message") or update.get("edited_message")
    if not isinstance(msg, dict):
        return None
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    contact = allowlisted_contact(svc.db, chat_id)
    if contact is None:
        if str(msg.get("text") or "").strip().startswith("/whoami"):
            return f"你嘅 chat id 係：{chat_id}"        # anyone; reveals nothing else (§9.2)
        return STOCK_REPLY                              # one fixed reply, then drop (INV-7)
    if isinstance(msg.get("voice"), dict):
        return await _on_voice(svc, contact, msg, msg["voice"])
    text = msg.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    if text.startswith("/"):
        return await _on_command(svc, contact, chat_id, text, msg)
    await svc.kernel.submit(_ev(svc, "family.text", contact["id"],
                                {"contact_id": contact["id"], "text": _clip(text, 600),
                                 "message_id": msg.get("message_id"), "via": "telegram"}))
    return None


def _ev(svc: Any, type_: str, corr: str | None, payload: dict[str, Any]) -> Any:
    from ahbo.core.events import Event
    return Event(type=type_, source="telegram", correlation_id=corr, payload=payload)


# ---------- inbound voice note (allowlisted family only)
def ffmpeg_to_aac(src: Path, out_path: Path) -> float | None:
    """OGG/Opus → AAC `.m4a` (§9.2); returns the ffprobe duration, or None if ffmpeg/ffprobe is unavailable."""
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-c:a", "aac", "-b:a", "64k",
                        str(out_path)], check=True, timeout=30, capture_output=True)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("ffmpeg voice conversion failed: %r", e)
        return None
    return ffprobe_duration(out_path)


def ffprobe_duration(path: Path) -> float | None:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                           capture_output=True, text=True, timeout=15, check=True)
        return round(float(r.stdout.strip()), 2)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def make_silent_m4a(out_path: Path, seconds: float) -> bool:
    """Silent AAC placeholder (fake-mode seeds; §26.3 speech fake)."""
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", f"anullsrc=r=24000:cl=mono", "-t", f"{max(0.5, seconds):.2f}",
                        "-c:a", "aac", "-b:a", "32k", str(out_path)],
                       check=True, timeout=30, capture_output=True)
        return out_path.is_file()
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("ffmpeg silent clip failed: %r", e)
        return False


def ensure_seed_voice() -> Path:
    """`seed/voice/demo_daughter.m4a` — a silent placeholder at the seed transcript's length, made once."""
    if SEED_VOICE.is_file():
        return SEED_VOICE
    seconds = 0.0
    try:
        from ahbo.core.config import load_seed
        vm = (load_seed("demo.yaml").get("voice_messages") or [{}])[0]
        seconds = max(1.2, len(str(vm.get("transcript") or "")) / 5.2)
    except Exception:
        seconds = 4.0
    make_silent_m4a(SEED_VOICE, seconds)
    return SEED_VOICE


async def _on_voice(svc: Any, contact: dict[str, Any], msg: dict[str, Any], voice: dict[str, Any]) -> str | None:
    channel = svc.extra.get("telegram_channel")
    file_id = str(voice.get("file_id") or "")
    if channel is None or not file_id:
        return None
    vm_id = new_id("vm")
    d = svc.settings.media_dir / "voice_in"
    d.mkdir(parents=True, exist_ok=True)
    raw = d / f"{vm_id}_raw.ogg"
    raw.write_bytes(await channel.download(file_id))
    final = d / f"{vm_id}.m4a"
    duration = ffmpeg_to_aac(raw, final)
    if duration is None:                       # ffmpeg missing/unusable: keep the original OGG
        final, duration = raw, float(voice.get("duration") or 2.0)
    else:
        raw.unlink(missing_ok=True)
    svc.db.execute(
        "INSERT INTO voice_messages(id, direction, from_name, to_name, contact_id, file_path, mime, duration_s, "
        "transcript, received_at, telegram_file_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (vm_id, "in", contact["name"], "王婆婆", contact["id"], str(final), "audio/mp4" if final.suffix == ".m4a"
         else "audio/ogg", duration, None, svc.clock.iso(), file_id))
    await svc.kernel.submit(_ev(svc, "family.voice_note", None,
                                {"contact_id": contact["id"], "voice_id": vm_id, "via": "telegram"}))
    return "收到你嘅語音訊息，會話俾王婆婆聽。"


# ---------- commands (§9.2 table)
def _med_lines(db: Database, when: str | None = None) -> list[str]:
    cat = {r["id"]: r for r in db.query("SELECT id, yue, dose FROM med_catalogue")}
    out = []
    sql = ("SELECT s.time, s.med_names, s.instructions_text, d.state FROM med_schedule s "
           "LEFT JOIN dose_events d ON d.slot_id=s.slot_id {when} WHERE s.active=1 ORDER BY s.time")
    rows = db.query(sql.format(when="AND d.date=?" if when else ""), (when,) if when else ())
    for r in rows:
        ids = loads(r["med_names"], [])
        names = "、".join(filter(None, (cat[i]["yue"] if i in cat else i for i in ids)))
        dose = " ".join(filter(None, (cat[i]["dose"] for i in ids if i in cat)))
        state = {"taken_self_reported": "已食", "missed": "冇食到"}.get(r["state"] or "", "")
        out.append(f"{r['time']} {names}{'（' + dose + '）' if dose else ''}{' ' + state if state else ''}")
    return out


def _status_text(svc: Any) -> str:
    db, clock = svc.db, svc.clock
    lines = ["阿寶報告："]
    meds = _med_lines(db, clock.today().isoformat())
    lines += meds or ["今日冇藥要食。"]
    inc = db.one("SELECT id, kind, state FROM incidents WHERE state NOT IN ('CLOSED','IDLE') "
                 "ORDER BY opened_at DESC LIMIT 1")
    lines.append("緊急事故：" + ("而家冇。" if inc is None else f"{inc['kind']}（{inc['state']}）"))
    sess = db.one("SELECT opened_at FROM sessions WHERE closed_at IS NOT NULL ORDER BY opened_at DESC LIMIT 1")
    lines.append("最後傾談：" + (sess["opened_at"][:16].replace("T", " ") if sess else "未有紀錄。"))
    beacons = db.query("SELECT id, battery, last_seen FROM beacons")
    for b in beacons:
        bat = f"{int(round(float(b['battery'] or 0) * 100))}%" if b.get("battery") is not None else "？"
        lines.append(f"長者機（{b['id']}）電量：{bat}")
    return "\n".join(lines)


async def _on_command(svc: Any, contact: dict[str, Any], chat_id: str, text: str, msg: dict[str, Any]) -> str:
    db, clock = svc.db, svc.clock
    cmd, _, rest = text.strip().partition(" ")
    rest = rest.strip()
    name = contact["name"]
    if cmd == "/start":
        return f"你好，{name}！呢度係阿寶嘅家庭頻道。/status 睇今日情況，/meds 睇食藥時間。"
    if cmd == "/whoami":
        return f"你嘅 chat id 係：{chat_id}"
    if cmd == "/meds":
        return "食藥時間表：\n" + ("\n".join(_med_lines(db)) or "（未有資料）")
    if cmd == "/status":
        return _status_text(svc)
    if cmd == "/fact":
        if not rest:
            return "請喺 /fact 後面寫低要記嘅嘢。"
        from ahbo.memory.store import MemoryRejected, remember
        try:
            remember(db, clock, rest, "other", source="telegram")
            return f"記低咗：{rest}"
        except MemoryRejected as e:
            return polite_reject(e.reason)
    if cmd == "/story":
        if not rest:
            return "請喺 /story 後面講低個故事。"
        from ahbo.memory.life_stories import add_life_story
        from ahbo.memory.store import MemoryRejected
        photo_id = await _save_story_photo(svc, msg)
        try:
            add_life_story(db, clock, rest, photo_id=photo_id)
            return "記低咗你嘅故事，多謝分享。"
        except MemoryRejected as e:
            return polite_reject(e.reason)
    return "唔知呢個指令。可以用：/status /meds /fact /story /whoami"


def polite_reject(reason: str) -> str:
    return {"pii": "呢句有私人資料，阿寶唔敢記住。可以講得簡單啲。", "too_long": "太長喇，試下分成兩句先。",
            "empty": "請寫低內容。"}.get(reason, "呢句記唔到，試下第個寫法。")


async def _save_story_photo(svc: Any, msg: dict[str, Any]) -> str | None:
    """Largest photo of the message → media row (kind photo) → its id (for /story)."""
    photos = [p for p in msg.get("photo") or [] if isinstance(p, dict) and p.get("file_id")]
    if not photos:
        return None
    best = max(photos, key=lambda p: int(p.get("width") or 0) * int(p.get("height") or 0))
    channel = svc.extra.get("telegram_channel")
    if channel is None:
        return None
    data = await channel.download(str(best["file_id"]))
    ph_id = new_id("ph")
    d = svc.settings.media_dir / "stories"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{ph_id}.jpg"
    path.write_bytes(data)
    svc.db.execute("INSERT INTO media(id, kind, path, created_at) VALUES (?,?,?,?)",
                   (ph_id, "photo", str(path), svc.clock.iso()))
    return ph_id


async def _save_story_photo_async(svc: Any, msg: dict[str, Any]) -> str | None:
    photos = [p for p in msg.get("photo") or [] if isinstance(p, dict) and p.get("file_id")]
    if not photos:
        return None
    best = max(photos, key=lambda p: int(p.get("width") or 0) * int(p.get("height") or 0))
    channel = svc.extra.get("telegram_channel")
    if channel is None:
        return None
    data = await channel.download(str(best["file_id"]))
    ph_id = new_id("ph")
    d = svc.settings.media_dir / "stories"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{ph_id}.jpg"
    path.write_bytes(data)
    svc.db.execute("INSERT INTO media(id, kind, path, created_at) VALUES (?,?,?,?)",
                   (ph_id, "photo", str(path), svc.clock.iso()))
    return ph_id


# ---------- callback buttons (§9.2: handled/resolved/call_mum/confirm)
async def _on_callback(svc: Any, cb: dict[str, Any]) -> str | None:
    frm = cb.get("from") or {}
    chat_id = str(((cb.get("message") or {}).get("chat") or {}).get("id") or frm.get("id") or "")
    contact = allowlisted_contact(svc.db, chat_id)
    if contact is None:
        await _answer(svc, cb, STOCK_REPLY)
        return STOCK_REPLY
    data = str(cb.get("data") or "")
    button, oid = parse_callback(data)
    payload: dict[str, Any] = {"contact_id": contact["id"], "button": button or data or None,
                               "callback_data": data or None, "via": "telegram"}
    if oid:
        payload["outbox_id"] = oid
    row = outbox.get(svc.db, oid) if oid else None
    corr = (row or {}).get("correlation_id")
    confirmed: list[str] = []
    if button == "confirm":
        confirmed = _confirm_facts(svc, row)
        payload["confirmed_fact_ids"] = confirmed
    await svc.kernel.submit(_ev(svc, "family.button", corr, payload))
    await _answer(svc, cb, "已確認" if button == "confirm" else "收到")
    return None


def _confirm_facts(svc: Any, row: dict[str, Any] | None) -> list[str]:
    """`confirm` on a digest row: activate the pending facts it carried (`payload.fact_ids`)."""
    if row is None:
        return []
    ids = list(row.get("payload", {}).get("fact_ids") or [])
    from ahbo.memory.store import confirm_fact
    return [fid for fid in ids if confirm_fact(svc.db, fid, True)]


async def _answer(svc: Any, cb: dict[str, Any], text: str) -> None:
    channel = svc.extra.get("telegram_channel")
    cid = str(cb.get("id") or "")
    if channel is not None and cid:
        await channel.answer_callback(cid, text)


@router.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> JSONResponse:
    svc = current_svc(request)
    secret = svc.settings.telegram_webhook_secret
    got = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not secret:
        return JSONResponse({"ok": False, "reason": "webhook_secret_not_configured"}, status_code=403)
    if not got:
        return JSONResponse({"ok": False, "reason": "missing_secret"}, status_code=401)
    if not hmac.compare_digest(str(secret), got):
        return JSONResponse({"ok": False, "reason": "bad_secret"}, status_code=403)
    try:
        update = await request.json()
    except ValueError:
        return JSONResponse({"ok": False, "reason": "bad_json"}, status_code=400)
    await handle_update(svc, update if isinstance(update, dict) else {})
    return JSONResponse({"ok": True})


# ==================================================================== owner setup (never called by the system)
def set_webhook(svc: Any) -> dict[str, Any]:
    """The setWebhook call for the owner's setup (§27.1 row 6). **Never run by the system; never run it here.**
    Returns the request the owner (or `make doctor`) would make. No network access happens in this function."""
    s: Settings = svc.settings
    return {"method": "POST", "url": f"https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook",
            "json": {"url": f"{s.public_base_url}/telegram/webhook", "secret_token": s.telegram_webhook_secret,
                     "allowed_updates": ["message", "callback_query"], "drop_pending_updates": True}}
