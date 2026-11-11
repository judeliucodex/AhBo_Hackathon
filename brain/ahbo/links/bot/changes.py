"""Every change 阿珍 makes from Telegram: describe (for the 「確認／取消」 step) and apply.

Data edits (medicine catalogue + schedule, calendar, quiet hours / check-ins / digest, contacts) are written here
and recorded as a Kernel event `settings.changed {what, before, after, by}` (source `telegram`; INV-6/INV-8).
Medicine data is carer-entered and may only change here (Po-po can never change it by voice). After a medicine
change the R-slot reminder clips are re-rendered (`ahbo.ops.render_fixed.ensure_manifest`) and the iPad gets
`device.config_update {reminder_mirror, fixed_cache_version}`. A quiet-hours change also mirrors into
`resident.quiet_hours` (read by the Device Hub's welcome config) and is pushed with `device.config_update`.

Demo/admin controls reuse `ahbo.api.demo` (each is an `operator.*` event there; fall/SOS carry source `operator`,
so the face card says 操作員觸發, INV-8). Each is preceded by a noted `operator.telegram` event naming who asked.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time
from typing import Any

from ahbo.core.clock import HKT
from ahbo.core.events import P1, Event, cmd
from ahbo.core.ids import new_id
from ahbo.core.jsonutil import dumps, loads
from ahbo.settings_store import effective, quiet_hours, set_values

from . import phones
from .views import CHECKIN_NAMES, KINDS, pill_label, slot_names

log = logging.getLogger("ahbo.links.bot.changes")


class BotError(Exception):
    """A change that can't be made; the message is shown to 阿珍 as is."""


# ================================================================ helpers
async def changed(svc: Any, contact: dict[str, Any], what: str, before: Any, after: Any) -> None:
    await svc.kernel.process(Event(type="settings.changed", source="telegram",
                                   payload={"what": what, "before": before, "after": after, "by": contact["id"]}))


def _med_row(db: Any, mid: str) -> dict[str, Any] | None:
    r = db.one("SELECT * FROM med_catalogue WHERE id=?", (mid,))
    if r:
        r = dict(r)
        r["pill"] = loads(r["pill"], [])
    return r


def _slot_row(db: Any, sid: str) -> dict[str, Any] | None:
    r = db.one("SELECT * FROM med_schedule WHERE slot_id=?", (sid,))
    if r:
        r = dict(r)
        r["med_names"] = loads(r["med_names"], [])
    return r


def _contact_public(c: dict[str, Any] | None) -> dict[str, Any] | None:
    if not c:
        return None
    return {"id": c["id"], "name": c["name"], "role": c["role"], "phone": phones.mask(c.get("phone")),
            "verified": int(c.get("verified") or 0), "priority": c["priority"],
            "telegram_chat_id": c.get("telegram_chat_id")}


def reminder_mirror(svc: Any) -> dict[str, Any]:
    """The Device Hub's `reminder_mirror` (one builder: `links.device_hub.DeviceHub.reminder_mirror`)."""
    hub = getattr(svc, "hub", None)
    fn = getattr(hub, "reminder_mirror", None)
    if callable(fn):
        return fn()
    from types import SimpleNamespace

    from ..device_hub import DeviceHub
    return DeviceHub.reminder_mirror(SimpleNamespace(db=svc.db, svc=svc))   # same code, no hub instance


def rerender(svc: Any) -> str | None:
    """Re-render the fixed clips (R_<slot> follow the schedule); returns the new manifest version."""
    from ahbo.ops import render_fixed
    m = render_fixed.ensure_manifest(svc)
    return str((m or {}).get("version") or "") or None


async def med_sync(svc: Any) -> dict[str, Any]:
    """After a medicine change: re-render R clips, then push the new reminder mirror (+ cache version)."""
    version = None
    try:
        version = await asyncio.to_thread(rerender, svc)
    except Exception:
        log.exception("re-render after a medicine change failed")
    payload: dict[str, Any] = {"reminder_mirror": reminder_mirror(svc)}
    if version:
        payload["fixed_cache_version"] = version
    await svc.kernel.emit(cmd("device.config_update", P1, **payload))
    return payload


def _spawn(svc: Any, coro: Awaitable[Any], name: str) -> None:
    task = svc.kernel.spawn(coro, name)
    tasks = [t for t in svc.extra.get("bot_tasks", []) if not t.done()]
    svc.extra["bot_tasks"] = [*tasks, task]          # tests await these


# ================================================================ medicine catalogue
def describe_med_upsert(svc: Any, a: dict[str, Any]) -> str:
    old = _med_row(svc.db, a["id"]) if a.get("id") else None
    new = f"{a['yue']}（{a.get('en') or '—'}）{a.get('dose') or ''}，藥丸：{pill_label(a['pill'])}"
    if old is None:
        return f"加藥物：{new}"
    was = f"{old['yue']}（{old.get('en') or '—'}）{old.get('dose') or ''}，藥丸：{pill_label(old['pill'])}"
    return f"改藥物：\n之前：{was}\n之後：{new}"


async def apply_med_upsert(svc: Any, contact: dict[str, Any], a: dict[str, Any]) -> str:
    db = svc.db
    mid = a.get("id") or new_id("m")
    before = _med_row(db, mid)
    db.execute("INSERT INTO med_catalogue(id,yue,en,dose,pill) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
               "yue=excluded.yue, en=excluded.en, dose=excluded.dose, pill=excluded.pill",
               (mid, a["yue"], a.get("en") or None, a.get("dose") or None, dumps(a["pill"])))
    after = _med_row(db, mid)
    await changed(svc, contact, "med_catalogue", before, after)
    _spawn(svc, med_sync(svc), "bot.med_sync")
    return ("已加" if before is None else "已改") + f"藥物：{a['yue']}。食藥提醒錄音會自動更新。"


def describe_med_delete(svc: Any, a: dict[str, Any]) -> str:
    m = _med_row(svc.db, a["id"])
    if m is None:
        raise BotError("搵唔到呢隻藥。")
    used = [s["time"] for s in svc.db.query("SELECT time, med_names FROM med_schedule")
            if a["id"] in loads(s["med_names"], [])]
    if used:
        raise BotError(f"{m['yue']}仲喺 {'、'.join(used)} 嘅時間表入面，請先喺時間表移除。")
    return f"刪除藥物：{m['yue']}"


async def apply_med_delete(svc: Any, contact: dict[str, Any], a: dict[str, Any]) -> str:
    describe_med_delete(svc, a)
    before = _med_row(svc.db, a["id"])
    svc.db.execute("DELETE FROM med_catalogue WHERE id=?", (a["id"],))
    await changed(svc, contact, "med_catalogue", before, None)
    _spawn(svc, med_sync(svc), "bot.med_sync")
    return f"已刪除藥物：{before['yue']}。"


# ================================================================ dose slots
def _slot_desc(db: Any, time_: str, meds: list[str], active: int = 1) -> str:
    return f"{time_} {slot_names(db, meds)}{'' if active else '（暫停）'}"


def _check_slot(db: Any, a: dict[str, Any]) -> None:
    if not a.get("meds"):
        raise BotError("每個時段最少要有一隻藥。")
    cat = {r["id"] for r in db.query("SELECT id FROM med_catalogue")}
    missing = [m for m in a["meds"] if m not in cat]
    if missing:
        raise BotError("有藥物唔喺藥物資料入面。")
    clash = db.one("SELECT slot_id FROM med_schedule WHERE time=? AND slot_id != ? AND active=1",
                   (a["time"], a.get("slot_id") or ""))
    if clash:
        raise BotError(f"{a['time']} 已經有一個食藥時段，請改嗰個。")


def describe_slot_upsert(svc: Any, a: dict[str, Any]) -> str:
    _check_slot(svc.db, a)
    old = _slot_row(svc.db, a["slot_id"]) if a.get("slot_id") else None
    new = _slot_desc(svc.db, a["time"], a["meds"])
    if old is None:
        return f"加食藥時間：{new}"
    return f"改食藥時間：\n之前：{_slot_desc(svc.db, old['time'], old['med_names'], old['active'])}\n之後：{new}"


async def apply_slot_upsert(svc: Any, contact: dict[str, Any], a: dict[str, Any]) -> str:
    db = svc.db
    _check_slot(db, a)
    sid = a.get("slot_id")
    if not sid:
        sid = base = "s_" + a["time"].replace(":", "")
        n = 1
        while db.one("SELECT 1 FROM med_schedule WHERE slot_id=?", (sid,)):
            n += 1
            sid = f"{base}_{n}"
    before = _slot_row(db, sid)
    if before is None:
        db.execute("INSERT INTO med_schedule(slot_id,time,med_names,active) VALUES (?,?,?,1)",
                   (sid, a["time"], dumps(a["meds"])))
    else:
        db.execute("UPDATE med_schedule SET time=?, med_names=? WHERE slot_id=?", (a["time"], dumps(a["meds"]), sid))
    after = _slot_row(db, sid)
    await changed(svc, contact, "med_schedule", before, after)
    _spawn(svc, med_sync(svc), "bot.med_sync")
    return f"已{'加' if before is None else '改'}食藥時間：{_slot_desc(db, a['time'], a['meds'])}。iPad 同提醒錄音會更新。"


def describe_slot_toggle(svc: Any, a: dict[str, Any]) -> str:
    s = _slot_row(svc.db, a["slot_id"])
    if s is None:
        raise BotError("搵唔到呢個時段。")
    return f"{'暫停' if s['active'] else '恢復'}食藥時間：{_slot_desc(svc.db, s['time'], s['med_names'])}"


async def apply_slot_toggle(svc: Any, contact: dict[str, Any], a: dict[str, Any]) -> str:
    db = svc.db
    before = _slot_row(db, a["slot_id"])
    if before is None:
        raise BotError("搵唔到呢個時段。")
    if not before["active"]:
        _check_slot(db, {"slot_id": before["slot_id"], "time": before["time"], "meds": before["med_names"]})
    db.execute("UPDATE med_schedule SET active=? WHERE slot_id=?", (0 if before["active"] else 1, a["slot_id"]))
    after = _slot_row(db, a["slot_id"])
    await changed(svc, contact, "med_schedule", before, after)
    _spawn(svc, med_sync(svc), "bot.med_sync")
    return f"已{'暫停' if before['active'] else '恢復'}：{_slot_desc(db, before['time'], before['med_names'])}。"


def describe_slot_delete(svc: Any, a: dict[str, Any]) -> str:
    s = _slot_row(svc.db, a["slot_id"])
    if s is None:
        raise BotError("搵唔到呢個時段。")
    return f"刪除食藥時間：{_slot_desc(svc.db, s['time'], s['med_names'], s['active'])}"


async def apply_slot_delete(svc: Any, contact: dict[str, Any], a: dict[str, Any]) -> str:
    before = _slot_row(svc.db, a["slot_id"])
    if before is None:
        raise BotError("搵唔到呢個時段。")
    svc.db.execute("DELETE FROM med_schedule WHERE slot_id=?", (a["slot_id"],))
    await changed(svc, contact, "med_schedule", before, None)
    _spawn(svc, med_sync(svc), "bot.med_sync")
    return f"已刪除食藥時間：{before['time']}。"


# ================================================================ calendar
def _cal_iso(day: str, hhmm: str) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.combine(date.fromisoformat(day), time(h, m), HKT).isoformat()


def _ev_desc(e: dict[str, Any]) -> str:
    dt = e.get("datetime") or _cal_iso(e["date"], e["time"])
    out = f"{dt[:10]} {dt[11:16]} {e['title']}"
    if e.get("place"):
        out += f"＠{e['place']}"
    out += f"（{KINDS.get(e.get('kind') or 'other', e.get('kind'))}）"
    if e.get("route"):
        out += f"，路線：{e['route']}"
    return out


def describe_cal_upsert(svc: Any, a: dict[str, Any]) -> str:
    old = svc.db.one("SELECT * FROM calendar_events WHERE id=?", (a["id"],)) if a.get("id") else None
    if a.get("id") and old is None:
        raise BotError("搵唔到呢個約會。")
    if old is None:
        return f"加約會：{_ev_desc(a)}"
    return f"改約會：\n之前：{_ev_desc(old)}\n之後：{_ev_desc(a)}"


async def apply_cal_upsert(svc: Any, contact: dict[str, Any], a: dict[str, Any]) -> str:
    db = svc.db
    eid = a.get("id") or new_id("cal")
    before = db.one("SELECT * FROM calendar_events WHERE id=?", (eid,))
    dt = _cal_iso(a["date"], a["time"])
    if before is None:
        db.execute("INSERT INTO calendar_events(id,datetime,title,place,kind,route,status,source) "
                   "VALUES (?,?,?,?,?,?,?,?)", (eid, dt, a["title"], a.get("place") or None, a["kind"],
                                                 a.get("route") or None, "confirmed", "telegram"))
    else:
        db.execute("UPDATE calendar_events SET datetime=?, title=?, place=?, kind=?, route=?, status='confirmed' "
                   "WHERE id=?", (dt, a["title"], a.get("place") or None, a["kind"], a.get("route") or None, eid))
    after = db.one("SELECT * FROM calendar_events WHERE id=?", (eid,))
    await changed(svc, contact, "calendar", before, after)
    return f"已{'加' if before is None else '改'}約會：{_ev_desc(after)}。"


def describe_cal_cancel(svc: Any, a: dict[str, Any]) -> str:
    e = svc.db.one("SELECT * FROM calendar_events WHERE id=?", (a["id"],))
    if e is None or e["status"] == "cancelled":
        raise BotError("搵唔到呢個約會（或者已經取消咗）。")
    return f"取消約會：{_ev_desc(e)}"


async def apply_cal_cancel(svc: Any, contact: dict[str, Any], a: dict[str, Any]) -> str:
    describe_cal_cancel(svc, a)
    before = svc.db.one("SELECT * FROM calendar_events WHERE id=?", (a["id"],))
    svc.db.execute("UPDATE calendar_events SET status='cancelled' WHERE id=?", (a["id"],))
    after = svc.db.one("SELECT * FROM calendar_events WHERE id=?", (a["id"],))
    await changed(svc, contact, "calendar", before, after)
    return f"已取消約會：{before['title']}。"


# ================================================================ quiet hours / check-ins / digest (overlay)
LABELS = {"quiet_start": "靜音開始", "quiet_end": "靜音結束", **CHECKIN_NAMES}


def describe_settings(svc: Any, a: dict[str, Any]) -> str:
    cur = effective(svc.db, svc.config)
    patch = a["patch"]
    merged = {**cur, **patch}
    if merged["quiet_start"] == merged["quiet_end"]:
        raise BotError("靜音開始同結束唔可以一樣。")
    return "改設定：\n" + "\n".join(f"{LABELS[k]}：{cur.get(k, '—')} → {v}" for k, v in patch.items())


def sync_resident_quiet(svc: Any) -> dict[str, str]:
    """Mirror the effective quiet hours into `resident.quiet_hours` (the Device Hub's welcome config reads it)."""
    q = quiet_hours(svc.db, svc.config)
    svc.db.execute("UPDATE resident SET quiet_hours=?", (dumps(q),))
    return q


async def apply_settings(svc: Any, contact: dict[str, Any], a: dict[str, Any]) -> str:
    describe_settings(svc, a)
    patch = a["patch"]
    before_all = effective(svc.db, svc.config)
    before = {k: before_all.get(k) for k in patch}
    set_values(svc.db, patch)
    after_all = effective(svc.db, svc.config)
    after = {k: after_all.get(k) for k in patch}
    what = "quiet_hours" if any(k.startswith("quiet") for k in patch) else "checkins"
    await changed(svc, contact, what, before, after)
    if what == "quiet_hours":
        q = sync_resident_quiet(svc)
        await svc.kernel.emit(cmd("device.config_update", P1, quiet_hours=q))
    lines = [f"{LABELS[k]}：{after[k]}" for k in patch]
    return "已改設定：" + "，".join(lines) + "。即刻生效。"


# ================================================================ contacts
def _check_contact(svc: Any, me: dict[str, Any], a: dict[str, Any]) -> None:
    db = svc.db
    if a.get("id"):
        old = db.one("SELECT * FROM contacts WHERE id=?", (a["id"],))
        if old is None or old["role"] != "family":
            raise BotError("只可以改家人聯絡人。")
        if old["id"] == me["id"] and str(a.get("chat") or "") != str(old.get("telegram_chat_id") or ""):
            raise BotError("唔可以改你自己嘅 Telegram chat id（會鎖住你自己）。")
    if a.get("phone"):
        e164, reason = phones.normalise(a["phone"])
        if e164 is None or e164 != a["phone"]:
            raise BotError(phones.reason_text(reason))
    if a.get("chat"):
        other = db.one("SELECT id FROM contacts WHERE telegram_chat_id=? AND id != ?", (str(a["chat"]),
                                                                                    a.get("id") or ""))
        if other:
            raise BotError("呢個 chat id 已經屬於另一個聯絡人。")


def describe_contact_upsert(svc: Any, a: dict[str, Any], me: dict[str, Any]) -> str:
    _check_contact(svc, me, a)
    new = (f"{a['name']}（家人），電話 {phones.mask(a.get('phone'))}，次序 {a['priority']}，"
           f"Telegram：{a.get('chat') or '—'}")
    warn = ["", "確認即係你確認呢個電話號碼啱，而且跌倒時阿寶會自動打俾佢。" if a.get("phone") else "",
            "⚠️ 有 Telegram chat id 嘅家人可以用呢個機械人睇同改所有嘢。" if a.get("chat") else ""]
    old = svc.db.one("SELECT * FROM contacts WHERE id=?", (a["id"],)) if a.get("id") else None
    head = f"加聯絡人：{new}" if old is None else (
        f"改聯絡人：\n之前：{old['name']}，電話 {phones.mask(old.get('phone'))}，次序 {old['priority']}，"
        f"Telegram：{old.get('telegram_chat_id') or '—'}\n之後：{new}")
    return head + "\n".join(w for w in warn if w is not None)


async def _verify_number(svc: Any, e164: str) -> tuple[int, str]:
    """(verified, note). Fake Twilio: the explicit confirm is enough. Real: the number must be a Caller ID."""
    if svc.settings.is_fake("twilio"):
        return 1, "電話已核實（示範模式，唔會真係打出去）。"
    ok = await phones.twilio_verified(svc, e164)
    if ok:
        return 1, "電話已核實 ✅（Twilio 已登記呢個號碼）。"
    if ok is None:
        return 0, "⚠️ 暫時查唔到 Twilio，電話未核實；遲啲喺聯絡人撳「☎️ 核實電話」再試。"
    return 0, ("⚠️ 電話未核實：Twilio 試用戶口只可以打已登記嘅號碼。請喺 Twilio 網頁 Phone Numbers → "
               "Verified Caller IDs 加呢個號碼，之後喺聯絡人撳「☎️ 核實電話」。核實之前阿寶唔會打俾佢。")


async def apply_contact_upsert(svc: Any, contact: dict[str, Any], a: dict[str, Any]) -> str:
    _check_contact(svc, contact, a)
    db = svc.db
    cid = a.get("id") or new_id("c")
    before = db.one("SELECT * FROM contacts WHERE id=?", (cid,))
    phone = a.get("phone") or None
    note = ""
    if not phone:
        verified = 0
    elif before is not None and before.get("phone") == phone and int(before.get("verified") or 0) == 1:
        verified = 1
    else:
        verified, note = await _verify_number(svc, phone)
    if before is None:
        db.execute("INSERT INTO contacts(id,name,role,relation,telegram_chat_id,phone,verified,priority) "
                   "VALUES (?,?,?,?,?,?,?,?)", (cid, a["name"], "family", None, a.get("chat") or None, phone,
                                                verified, int(a["priority"])))
    else:
        db.execute("UPDATE contacts SET name=?, telegram_chat_id=?, phone=?, verified=?, priority=? WHERE id=?",
                   (a["name"], a.get("chat") or None, phone, verified, int(a["priority"]), cid))
    after = db.one("SELECT * FROM contacts WHERE id=?", (cid,))
    await changed(svc, contact, "contacts", _contact_public(before), _contact_public(after))
    return f"已{'加' if before is None else '改'}聯絡人：{a['name']}。" + (f"\n{note}" if note else "")


def describe_contact_delete(svc: Any, a: dict[str, Any], me: dict[str, Any]) -> str:
    c = svc.db.one("SELECT * FROM contacts WHERE id=?", (a["id"],))
    if c is None or c["role"] != "family":
        raise BotError("只可以刪除家人聯絡人。")
    if c["id"] == me["id"]:
        raise BotError("唔可以刪除你自己（會鎖住你自己）。")
    return f"刪除聯絡人：{c['name']}（電話 {phones.mask(c.get('phone'))}）"


async def apply_contact_delete(svc: Any, contact: dict[str, Any], a: dict[str, Any]) -> str:
    describe_contact_delete(svc, a, contact)
    before = svc.db.one("SELECT * FROM contacts WHERE id=?", (a["id"],))
    svc.db.execute("DELETE FROM contacts WHERE id=?", (a["id"],))
    await changed(svc, contact, "contacts", _contact_public(before), None)
    return f"已刪除聯絡人：{before['name']}。"


def describe_verify(svc: Any, a: dict[str, Any]) -> str:
    c = svc.db.one("SELECT * FROM contacts WHERE id=?", (a["id"],))
    if c is None or c["role"] != "family":
        raise BotError("搵唔到呢個聯絡人。")
    e164, reason = phones.normalise(c.get("phone"))
    if e164 is None:
        raise BotError(f"{c['name']}嘅電話用唔到：{phones.reason_text(reason)}請先改電話。")
    return (f"核實 {c['name']} 嘅電話 {phones.mask(e164)}？\n確認即係你確認呢個號碼啱，"
            "跌倒時阿寶會自動打俾佢。")


async def apply_verify(svc: Any, contact: dict[str, Any], a: dict[str, Any]) -> str:
    describe_verify(svc, a)
    c = svc.db.one("SELECT * FROM contacts WHERE id=?", (a["id"],))
    e164, _ = phones.normalise(c.get("phone"))
    verified, note = await _verify_number(svc, e164)
    before = _contact_public(c)
    svc.db.execute("UPDATE contacts SET phone=?, verified=? WHERE id=?", (e164, verified, c["id"]))
    after = _contact_public(svc.db.one("SELECT * FROM contacts WHERE id=?", (c["id"],)))
    await changed(svc, contact, "contacts", before, after)
    return note


# ================================================================ demo / admin controls (ahbo.api.demo)
ADMIN_DESC = {
    "reset": "重設示範（藥物、日程、聯絡人會返去示範資料；設定時間保留）",
    "panic": "全部停止（停聲、收線、表情返待機）",
    "fall": "模擬跌倒（操作員觸發）", "sos": "模擬求助（操作員觸發）",
    "plan": "即刻做 07:00 計劃", "mute": "靜音阿寶", "unmute": "取消靜音",
    "clock_now": "時間返去真實時間",
}


THINKING = {"flash": ("off", "low", "high", "max"), "sonnet": ("off", "low", "medium", "high", "xhigh", "max")}


def describe_admin(svc: Any, op: str, a: dict[str, Any]) -> str:
    if op in ADMIN_DESC:
        return ADMIN_DESC[op]
    if op == "scen":
        return f"行示範場景 {a['name']}（會先重設示範）"
    if op == "clock":
        return f"較虛擬時間：{a['label']}"
    if op == "dose":
        s = svc.db.one("SELECT time FROM med_schedule WHERE slot_id=?", (a["slot_id"],))
        if s is None:
            raise BotError("搵唔到呢個時段。")
        return f"即刻提醒食 {s['time']} 嘅藥"
    if op == "ck":
        if a.get("kind") not in ("morning", "afternoon", "evening"):
            raise BotError("唔知呢個問候。")
        return f"即刻做{CHECKIN_NAMES[a['kind']]}"
    if op == "say":
        return f"阿寶講（標明操作員）：「{a['text']}」"
    if op in ("flash", "sonnet"):
        if a.get("level") not in THINKING[op]:
            raise BotError("唔知呢個思考程度。")
        return f"{'Flash' if op == 'flash' else 'Sonnet'} 思考程度 → {a['level']}"
    raise BotError("唔知呢個操作。")


async def apply_admin(svc: Any, contact: dict[str, Any], op: str, a: dict[str, Any]) -> str:
    from ahbo.api import demo
    svc.kernel.note(Event(type="operator.telegram", source="operator",
                          payload={"op": op, "args": a, "by": contact["id"], "via": "telegram"}))
    if op == "reset":
        r = await demo.reset_demo(svc)
        sync_resident_quiet(svc)
        return f"已重設示範（{r.get('ms', '?')} ms）。"
    if op == "panic":
        await demo.panic(svc)
        return "已全部停止。"
    if op in ("fall", "sos", "plan"):
        await demo.trigger(svc, {"kind": {"plan": "plan_now"}.get(op, op)})
        return {"fall": "已模擬跌倒（操作員觸發）。你會收到通知。", "sos": "已模擬求助（操作員觸發）。",
                "plan": "已開始做今日計劃。"}[op]
    if op == "dose":
        await demo.trigger(svc, {"kind": "dose_now", "slot_id": a["slot_id"]})
        return "已即刻提醒食藥。"
    if op == "ck":
        await demo.trigger(svc, {"kind": "checkin", "checkin": a["kind"]})
        return f"已開始{CHECKIN_NAMES.get(a['kind'], '')}。"
    if op == "scen":
        r = demo.start_scenario(svc, a["name"])
        if not r.get("ok"):
            raise BotError("唔知呢個場景。")
        return f"開始行 {a['name']}。"
    if op == "clock":
        r = await demo.set_clock(svc, a["body"])
        if not r.get("ok"):
            raise BotError("較唔到時間。")
        return f"虛擬時間而家係 {r['now'][11:16]}。"
    if op == "clock_now":
        r = await demo.set_clock(svc, {"offset_s": 0})
        return f"返咗真實時間：{r['now'][11:16]}。"
    if op in ("mute", "unmute"):
        await demo.override(svc, {"kind": op})
        return "阿寶已靜音。" if op == "mute" else "阿寶已取消靜音。"
    if op == "say":
        r = await demo.override(svc, {"kind": "say", "text": a["text"]})
        if not r.get("ok"):
            raise BotError("講唔到。")
        return "阿寶講咗（標明操作員）。"
    if op in ("flash", "sonnet"):
        await demo.override(svc, {"kind": "knobs", f"{op}_thinking": a["level"]})
        return f"{'Flash' if op == 'flash' else 'Sonnet'} 思考程度已改做 {a['level']}。"
    raise BotError("唔知呢個操作。")


# ================================================================ registry
Describe = Callable[[Any, dict[str, Any], dict[str, Any]], str]
Apply = Callable[[Any, dict[str, Any], dict[str, Any]], Awaitable[str]]

DATA_OPS: dict[str, tuple[Describe, Apply]] = {
    "med_upsert": (lambda s, a, me: describe_med_upsert(s, a), apply_med_upsert),
    "med_delete": (lambda s, a, me: describe_med_delete(s, a), apply_med_delete),
    "slot_upsert": (lambda s, a, me: describe_slot_upsert(s, a), apply_slot_upsert),
    "slot_toggle": (lambda s, a, me: describe_slot_toggle(s, a), apply_slot_toggle),
    "slot_delete": (lambda s, a, me: describe_slot_delete(s, a), apply_slot_delete),
    "cal_upsert": (lambda s, a, me: describe_cal_upsert(s, a), apply_cal_upsert),
    "cal_cancel": (lambda s, a, me: describe_cal_cancel(s, a), apply_cal_cancel),
    "settings": (lambda s, a, me: describe_settings(s, a), apply_settings),
    "contact_upsert": (describe_contact_upsert, apply_contact_upsert),
    "contact_delete": (describe_contact_delete, apply_contact_delete),
    "verify": (lambda s, a, me: describe_verify(s, a), apply_verify),
}
ADMIN_OPS = ("reset", "panic", "fall", "sos", "plan", "dose", "ck", "scen", "clock", "clock_now", "mute", "unmute",
             "say", "flash", "sonnet")


def describe(svc: Any, me: dict[str, Any], op: str, a: dict[str, Any]) -> str:
    if op in DATA_OPS:
        return DATA_OPS[op][0](svc, a, me)
    if op in ADMIN_OPS:
        return describe_admin(svc, op, a)
    raise BotError("唔知呢個操作。")


async def apply(svc: Any, me: dict[str, Any], op: str, a: dict[str, Any]) -> str:
    if op in DATA_OPS:
        return await DATA_OPS[op][1](svc, me, a)
    if op in ADMIN_OPS:
        return await apply_admin(svc, me, op, a)
    raise BotError("唔知呢個操作。")

