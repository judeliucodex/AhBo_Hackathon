"""Family Link over Telegram (§9.2, D7): the real and the fake channel plus the webhook handler.

Real channel: the raw Bot API over `httpx` (no extra SDK). Fake channel: delivery is the `outbox` row the
dispatcher already wrote (§26.3), so `send` only confirms it.

Inbound (`POST /telegram/webhook`, this module's router):
- the `X-Telegram-Bot-Api-Secret-Token` header is checked with `hmac.compare_digest` (wrong/missing → 401/403);
- **allowlist by chat id** (`contacts.telegram_chat_id`): anyone else gets exactly one fixed reply
  「呢個係私人家庭機械人。」 and is dropped — never forwarded to the Kernel/agent (INV-7), except `/whoami`
  (anyone; the reply carries their chat id only);
- text → `family.text` (the Kernel quotes it, never an instruction) unless it answers an open bot form/confirm;
  voice notes → OGG download → ElevenLabs batch STT (real speech only; stored in Traditional) → ffmpeg AAC →
  `voice_messages` row → `family.voice_note`; alert buttons (`b:…`) →
  `family.button` (+ `answerCallbackQuery`; `confirm` confirms pending facts; `voice_mum` tells her to send a voice
  note); `/whoami /fact /story` here, every other command and every `m:` button → the caregiver/admin bot
  (`ahbo.links.bot`: menus, views, edits with 確認／取消, demo controls).
- every reply goes out through the channel (`channel.reply(chat_id, text, buttons)`); the fake channel records it.
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

from . import bot, current_svc, outbox
from .bot.ui import Reply
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
        self.replies: list[dict[str, Any]] = []

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

    async def reply(self, chat_id: str, text: str, buttons: list[list[dict[str, str]]] | None = None) -> None:
        """Fake delivery of a bot reply: recorded in memory (tests read `replies`), never sent."""
        self.replies.append({"chat_id": str(chat_id), "text": text, "buttons": buttons or []})
        del self.replies[:-200]
        log.info("fake telegram reply to %s: %s", chat_id, text[:80])


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
            data = {"chat_id": chat_id, "text": text}
            if markup:
                data["reply_markup"] = dumps(markup)   # null reply_markup → Telegram 400 "object expected as reply markup"
            res = await self._api("sendMessage", data=data)
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

    async def reply(self, chat_id: str, text: str, buttons: list[list[dict[str, str]]] | None = None) -> None:
        if not chat_id:
            return
        data: dict[str, Any] = {"chat_id": chat_id, "text": text[:4000]}
        if buttons:
            data["reply_markup"] = dumps({"inline_keyboard": buttons})
        try:
            await self._api("sendMessage", data=data)
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
    """One Telegram update → Kernel events / DB rows / bot replies. Returns the (first) reply text; every reply is
    also delivered through the channel (`channel.reply`; the fake channel records it, the real one sends it).

    Returns None for updates that are dropped (unknown payloads). Safe: never raises to the caller.
    """
    try:
        chat_id, replies = await _handle(svc, update)
    except Exception:
        log.exception("telegram update handling failed")
        return None
    if not replies:
        return None
    if isinstance(replies, Delivered):
        return str(replies)
    if isinstance(replies, str):
        replies = [Reply(replies)]
    channel = (getattr(svc, "extra", None) or {}).get("telegram_channel")
    if channel is not None and chat_id:
        for r in replies:
            try:
                await channel.reply(chat_id, r.text, r.markup() or None)
            except Exception:
                log.exception("telegram reply failed")
    return replies[0].text


class Delivered(str):
    """A reply text already delivered another way (e.g. as the callback answer): returned, not sent again."""


def _chat_of_update(update: dict[str, Any]) -> str:
    cb = update.get("callback_query")
    if isinstance(cb, dict):
        return str(((cb.get("message") or {}).get("chat") or {}).get("id") or (cb.get("from") or {}).get("id") or "")
    msg = update.get("message") or update.get("edited_message") or {}
    return str(((msg.get("chat") or {}) if isinstance(msg, dict) else {}).get("id") or "")


async def _handle(svc: Any, update: dict[str, Any]) -> tuple[str, list[Reply] | str | None]:
    if not isinstance(update, dict):
        return "", None
    chat_id = _chat_of_update(update)
    cb = update.get("callback_query")
    if isinstance(cb, dict):
        return chat_id, await _on_callback(svc, cb)
    msg = update.get("message") or update.get("edited_message")
    if not isinstance(msg, dict):
        return chat_id, None
    contact = allowlisted_contact(svc.db, chat_id)
    if contact is None:
        if str(msg.get("text") or "").strip().startswith("/whoami"):
            return chat_id, f"你嘅 chat id 係：{chat_id}"        # anyone; reveals nothing else (§9.2)
        return chat_id, STOCK_REPLY                              # one fixed reply, then drop (INV-7)
    if isinstance(msg.get("voice"), dict):
        return chat_id, await _on_voice(svc, contact, msg, msg["voice"])
    text = msg.get("text")
    if not isinstance(text, str) or not text.strip():
        text = msg.get("caption") if isinstance(msg.get("caption"), str) else None
        if not text or not text.startswith("/"):
            return chat_id, None
    if text.startswith("/"):
        return chat_id, await _on_command(svc, contact, chat_id, text, msg)
    answered = await bot.text(svc, contact, chat_id, text)        # an open edit form / pending confirm
    if answered is not None:
        return chat_id, answered
    from ahbo.agent.caregiver import caregiver_reply              # deferred: agent ↔ links must not cycle
    svc.spawn(caregiver_reply(svc, contact, chat_id, text), name="caregiver_chat")
    await svc.kernel.submit(_ev(svc, "family.text", contact["id"],
                                {"contact_id": contact["id"], "text": _clip(text, 600),
                                 "message_id": msg.get("message_id"), "via": "telegram"}))
    return chat_id, None


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


VOICE_STT_TIMEOUT_S = 25.0


async def transcribe_voice_note(svc: Any, audio: bytes, filename: str) -> str | None:
    """ElevenLabs batch STT (`speech.stt.file_model`, `yue`) of a family voice note, stored in Traditional
    (`speech.transcript.to_hk`). Only when `speech` is real and the provider is elevenlabs; any failure → None
    (no transcript is better than a wrong one: the face then shows just the waveform card, §23.5)."""
    settings = svc.settings
    if settings.is_fake("speech") or not settings.elevenlabs_api_key or not audio:
        return None
    import asyncio

    from ahbo.speech.config import speech_cfg
    from ahbo.speech.elevenlabs_stt import transcribe_file
    from ahbo.speech.transcript import to_hk

    cfg = speech_cfg(getattr(svc, "config", None)).stt
    if cfg.provider != "elevenlabs":
        return None
    try:
        j = await asyncio.wait_for(
            transcribe_file(audio, filename, api_key=settings.elevenlabs_api_key, cfg=cfg, content_type="audio/ogg",
                            client=svc.extra.get("elevenlabs_http")), VOICE_STT_TIMEOUT_S)
    except Exception as e:                   # SttError, timeout: never block the voice note itself
        log.warning("voice-note transcription failed (%s)", type(e).__name__)
        return None
    text = to_hk(str(j.get("text") or "").strip())
    return text or None


async def _on_voice(svc: Any, contact: dict[str, Any], msg: dict[str, Any], voice: dict[str, Any]) -> str | None:
    channel = svc.extra.get("telegram_channel")
    file_id = str(voice.get("file_id") or "")
    if channel is None or not file_id:
        return None
    vm_id = new_id("vm")
    d = svc.settings.media_dir / "voice_in"
    d.mkdir(parents=True, exist_ok=True)
    raw = d / f"{vm_id}_raw.ogg"
    audio = await channel.download(file_id)
    raw.write_bytes(audio)
    transcript = await transcribe_voice_note(svc, audio, raw.name)
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
         else "audio/ogg", duration, transcript, svc.clock.iso(), file_id))
    armed = _voice_mum_armed(svc, contact)
    await svc.kernel.submit(_ev(svc, "family.voice_note", None,
                                {"contact_id": contact["id"], "voice_id": vm_id, "via": "telegram",
                                 **({"voice_mum": True} if armed else {})}))
    if armed:
        return "✅ 收到，已經話俾王婆婆聽有段錄音等緊佢。"
    return "收到你嘅語音訊息，會話俾王婆婆聽。"


def _arm_voice_mum(svc: Any, contact_id: str) -> None:
    """Arm the one-shot voice-note mode: the caregiver's NEXT voice note announces to Po-po at P1."""
    svc.db.kv_set("voice_mum_armed", f"{contact_id}|{svc.clock.now().timestamp()}")


def on_playback_done(svc: Any, cmd: Any) -> None:
    """Caregiver confirmation: their voice note finished playing (or gave up) on Po-po's device.
    Sync kernel dispatcher → spawns the async send. Only real, recent caregiver notes confirm —
    injected/console demo notes (kv `voice_note_demo`) and anything older than 15 min stay silent."""
    p = cmd.payload
    mid = p.get("message_id")
    if not mid:
        return
    db = svc.db
    row = db.one("SELECT * FROM voice_messages WHERE id=?", (mid,))
    if row is None or row.get("direction") != "in":
        return
    if db.kv_get("voice_note_demo") == mid:
        return                                   # console/S1 demo injection: stay silent
    try:
        from datetime import datetime
        age = svc.clock.now() - datetime.fromisoformat(str(row["received_at"]))
        if age.total_seconds() > 15 * 60:
            return
    except (ValueError, TypeError):
        return
    contact = db.one("SELECT id, telegram_chat_id FROM contacts WHERE id=?", (row["contact_id"],))
    channel = svc.extra.get("telegram_channel")
    if contact is None or not contact["telegram_chat_id"] or channel is None:
        return
    text = ("✅ 媽媽啱啱聽完你嘅錄音喇。") if p.get("ok") \
        else ("⚠️ 暫時播唔到（iPad 離線？）。段錄音留咗喺度，阿寶遲啲會再試播俾佢聽。")
    hhmm = svc.clock.now().strftime("%H:%M")

    async def _send() -> None:
        try:
            await channel.send(kind="INFO", contact_id=contact["id"],
                               chat_id=str(contact["telegram_chat_id"]),
                               text=f"{text}（{hhmm}）")
        except Exception as e:
            log.warning("voice-note confirmation failed (%s)", type(e).__name__)

    svc.spawn(_send(), name="voice_confirm")


def _voice_mum_armed(svc: Any, contact: dict[str, Any]) -> bool:
    raw = svc.db.kv_get("voice_mum_armed")
    svc.db.kv_set("voice_mum_armed", "")                   # one-shot: armed covers exactly one note
    if not raw:
        return False
    cid, _, ts = str(raw).rpartition("|")
    try:
        age = svc.clock.now().timestamp() - float(ts)
    except ValueError:
        return False
    return cid == contact["id"] and 0 <= age <= 600        # expires after 10 minutes


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


async def _on_command(svc: Any, contact: dict[str, Any], chat_id: str, text: str,
                      msg: dict[str, Any]) -> list[Reply] | str:
    """/whoami /fact /story here; everything else (menus, views, edits, demo controls) is the bot (`links.bot`)."""
    db, clock = svc.db, svc.clock
    cmd, _, rest = text.strip().partition(" ")
    cmd = cmd.split("@", 1)[0].lower()
    rest = rest.strip()
    if cmd == "/whoami":
        return f"你嘅 chat id 係：{chat_id}"
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
    if cmd in ("/voicenote", "/voice"):
        _arm_voice_mum(svc, contact["id"])
        return VOICE_MUM_REPLY
    return await bot.command(svc, contact, chat_id, text)


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


# ---------- callback buttons: alert buttons `b:` (handled/resolved/voice_mum/confirm) and bot buttons `m:`
VOICE_MUM_REPLY = ("而家可以直接錄一段語音訊息俾媽媽（撳住咪高峰講），阿寶會即刻播俾佢聽。"
                   "（電話打唔到 iPad，所以用錄音代替。）")


async def _on_callback(svc: Any, cb: dict[str, Any]) -> list[Reply] | str | None:
    frm = cb.get("from") or {}
    chat_id = str(((cb.get("message") or {}).get("chat") or {}).get("id") or frm.get("id") or "")
    contact = allowlisted_contact(svc.db, chat_id)
    if contact is None:
        await _answer(svc, cb, STOCK_REPLY)
        return Delivered(STOCK_REPLY)             # the callback answer *is* the one fixed reply (INV-7)
    data = str(cb.get("data") or "")
    if data.startswith("m:"):
        await _answer(svc, cb, "收到")
        return await bot.callback(svc, contact, chat_id, data)
    button, oid = parse_callback(data)
    if button == "call_mum":                      # stale pre-11-Nov alert buttons
        button = "voice_mum"
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
    if button == "voice_mum":
        _arm_voice_mum(svc, contact["id"])
        return [Reply(VOICE_MUM_REPLY)]
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
