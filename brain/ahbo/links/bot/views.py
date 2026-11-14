"""Read-only views for 阿珍 (no Kernel events; nothing here changes state)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from ahbo.core.jsonutil import loads
from ahbo.settings_store import checkin_times, overlay, quiet_hours

from .phones import mask
from .ui import Reply, back_row, cb, rows

PILLS = {"--pill-a": "白", "--pill-b": "黃", "--pill-c": "粉紅"}
DOSE_STATES = {"scheduled": "未到", "reminding": "提醒緊", "snoozed": "遲啲先食", "taken_self_reported": "已食 ✅",
               "missed": "冇食到 ⚠️"}
KINDS = {"medical": "醫療", "meal": "飲食", "plan": "出門", "activity": "活動", "other": "其他"}
CHECKIN_NAMES = {"morning": "朝早問候", "afternoon": "下晝問候", "evening": "夜晚問候", "digest": "每日摘要"}
WEEKDAYS = "一二三四五六日"


def pill_label(pill: Any) -> str:
    toks = loads(pill, []) if isinstance(pill, str) else list(pill or [])
    return "／".join(PILLS.get(t, "?") for t in toks) or "—"


def catalogue(db: Any) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in db.query("SELECT * FROM med_catalogue ORDER BY id")}


def slot_names(db: Any, med_ids: list[str], cat: dict[str, Any] | None = None) -> str:
    cat = cat if cat is not None else catalogue(db)
    return "、".join(cat[i]["yue"] if i in cat else i for i in med_ids) or "（冇藥）"


def _hm(iso: str | None) -> str:
    return (iso or "")[11:16]


def _day_label(day: str, today: str) -> str:
    try:
        d = datetime.fromisoformat(day).date()
        t = datetime.fromisoformat(today).date()
    except ValueError:
        return day
    rel = {0: "今日", 1: "聽日", 2: "後日"}.get((d - t).days, "")
    return f"{d.month}月{d.day}日（{WEEKDAYS[d.weekday()]}）{rel}"


# ---------------------------------------------------------------- 今日狀況
def doses_today(svc: Any) -> list[str]:
    db = svc.db
    today = svc.clock.today().isoformat()
    ev = {r["slot_id"]: r for r in db.query("SELECT * FROM dose_events WHERE date=?", (today,))}
    cat = catalogue(db)
    out = []
    for s in db.query("SELECT * FROM med_schedule WHERE active=1 ORDER BY time"):
        st = (ev.get(s["slot_id"]) or {}).get("state", "scheduled")
        out.append(f"• {s['time']} {slot_names(db, loads(s['med_names'], []), cat)}：{DOSE_STATES.get(st, st)}")
    return out


def events_between(db: Any, start: str, end: str) -> list[dict[str, Any]]:
    return db.query("SELECT * FROM calendar_events WHERE substr(datetime,1,10) >= ? AND substr(datetime,1,10) <= ? "
                    "AND status != 'cancelled' ORDER BY datetime", (start, end))


def today_view(svc: Any) -> Reply:
    db, clock = svc.db, svc.clock
    now = clock.now()
    today = now.date().isoformat()
    lines = [f"📅 今日狀況（{now.strftime('%H:%M')}，{_day_label(today, today)}）", "", "💊 食藥："]
    lines += doses_today(svc) or ["• 今日冇藥要食。"]
    lines += ["", "🗓 約會："]
    evs = events_between(db, today, today)
    lines += [f"• {_hm(e['datetime'])} {e['title']}" + (f"＠{e['place']}" if e.get("place") else "") for e in evs] \
        or ["• 今日冇約會。"]
    inc = db.one("SELECT id, kind, state, source, opened_at FROM incidents WHERE closed_at IS NULL "
                 "AND state NOT IN ('CLOSED','IDLE') ORDER BY opened_at DESC LIMIT 1")
    lines += ["", "🚨 事故：" + ("而家冇。" if inc is None else
                                f"{ {'fall': '跌倒', 'sos': '求助'}.get(inc['kind'], inc['kind']) }"
                                f"（{inc['state']}，{_hm(inc['opened_at'])}"
                                f"{'，操作員觸發' if inc.get('source') == 'operator' else ''}）")]
    sess = db.one("SELECT opened_at FROM sessions WHERE reason != 'seed' ORDER BY opened_at DESC LIMIT 1") \
        or db.one("SELECT opened_at FROM sessions ORDER BY opened_at DESC LIMIT 1")
    lines.append("💬 最後傾談：" + (sess["opened_at"][:16].replace("T", " ") if sess else "未有紀錄。"))
    hub = getattr(svc, "hub", None)
    online = getattr(hub, "connected_count", None)
    if not isinstance(online, int):
        online = db.one("SELECT COUNT(*) n FROM devices WHERE online=1")["n"]
    lines.append("📱 iPad：" + ("在線 ✅" if online else "離線 ⚠️"))
    beacons = db.query("SELECT id, battery, last_seen FROM beacons ORDER BY id")
    if not beacons:
        lines.append("📟 長者機：未設定。")
    for b in beacons:
        bat = f"{int(round(float(b['battery']) * 100))}%" if b.get("battery") is not None else "？"
        seen = (b.get("last_seen") or "")[11:16] or "未見過"
        lines.append(f"📟 長者機 {b['id']}：電量 {bat}，最後訊號 {seen}")
    knobs = db.kv_get("knobs", {}) or {}
    if knobs.get("muted"):
        lines.append("🔇 阿寶而家靜音緊。")
    return Reply("\n".join(lines), [[("🎤 錄段聲俾媽媽", cb("vn")), ("🔔 最近通知", cb("v", "alerts"))],
                                    [("🔄 更新", cb("v", "today"))], back_row()])


# ---------------------------------------------------------------- 藥物
def meds_view(svc: Any) -> Reply:
    db = svc.db
    cat = catalogue(db)
    lines = ["💊 食藥時間表："]
    btns: list[tuple[str, str]] = []
    for s in db.query("SELECT * FROM med_schedule ORDER BY time"):
        off = "" if s["active"] else "（暫停）"
        lines.append(f"• {s['time']}{off} {slot_names(db, loads(s['med_names'], []), cat)}")
        btns.append((f"⏰ {s['time']}{off}", cb("v", "slot", s["slot_id"])))
    if len(lines) == 1:
        lines.append("（未有）")
    lines += ["", "📦 藥物資料："]
    for m in cat.values():
        lines.append(f"• {m['yue']}（{m.get('en') or '—'}）{m.get('dose') or ''}，藥丸：{pill_label(m['pill'])}")
        btns.append((f"💊 {m['yue']}", cb("v", "med", m["id"])))
    if not cat:
        lines.append("（未有）")
    kb = rows(btns, 3) + [[("➕ 加食藥時間", cb("f", "slot_add")), ("➕ 加藥物", cb("f", "med_add"))], back_row()]
    return Reply("\n".join(lines), kb)


def slot_view(svc: Any, slot_id: str) -> Reply:
    s = svc.db.one("SELECT * FROM med_schedule WHERE slot_id=?", (slot_id,))
    if s is None:
        return Reply("搵唔到呢個時段。", [back_row(("💊 藥物", cb("v", "meds")))])
    text = (f"⏰ {s['time']}：{slot_names(svc.db, loads(s['med_names'], []))}\n"
            f"狀態：{'生效' if s['active'] else '暫停'}")
    return Reply(text, [[("✏️ 改時間／藥", cb("f", "slot_edit", slot_id)),
                         ("⏸ 暫停" if s["active"] else "▶️ 恢復", cb("a", "slot_toggle", slot_id))],
                        [("🗑 刪除", cb("a", "slot_del", slot_id)), ("💊 藥物", cb("v", "meds"))], back_row()])


def med_view(svc: Any, med_id: str) -> Reply:
    m = svc.db.one("SELECT * FROM med_catalogue WHERE id=?", (med_id,))
    if m is None:
        return Reply("搵唔到呢隻藥。", [back_row(("💊 藥物", cb("v", "meds")))])
    text = (f"💊 {m['yue']}\n英文名：{m.get('en') or '—'}\n份量：{m.get('dose') or '—'}\n"
            f"藥丸顏色：{pill_label(m['pill'])}")
    return Reply(text, [[("✏️ 修改", cb("f", "med_edit", med_id)), ("🗑 刪除", cb("a", "med_del", med_id))],
                        back_row(("💊 藥物", cb("v", "meds")))])


# ---------------------------------------------------------------- 日程
def cal_view(svc: Any) -> Reply:
    db = svc.db
    today = svc.clock.today()
    end = today + timedelta(days=7)
    evs = events_between(db, today.isoformat(), end.isoformat())
    lines = ["🗓 日程（今日同之後七日）："]
    btns = []
    day = None
    for e in evs:
        d = e["datetime"][:10]
        if d != day:
            day = d
            lines += ["", _day_label(d, today.isoformat())]
        place = f"＠{e['place']}" if e.get("place") else ""
        lines.append(f"• {_hm(e['datetime'])} {e['title']}{place}（{KINDS.get(e['kind'], e['kind'])}）")
        btns.append((f"{d[5:]} {_hm(e['datetime'])} {e['title']}"[:30], cb("v", "ev", e["id"])))
    if not evs:
        lines.append("（冇約會）")
    return Reply("\n".join(lines), rows(btns, 2) + [[("➕ 加約會", cb("f", "cal_add"))], back_row()])


def event_view(svc: Any, ev_id: str) -> Reply:
    e = svc.db.one("SELECT * FROM calendar_events WHERE id=?", (ev_id,))
    if e is None:
        return Reply("搵唔到呢個約會。", [back_row(("🗓 日程", cb("v", "cal")))])
    text = (f"🗓 {e['datetime'][:10]} {_hm(e['datetime'])} {e['title']}\n地點：{e.get('place') or '—'}\n"
            f"類別：{KINDS.get(e['kind'], e['kind'])}\n路線：{e.get('route') or '—'}\n"
            f"狀態：{'已取消' if e['status'] == 'cancelled' else '有效'}")
    return Reply(text, [[("✏️ 修改", cb("f", "cal_edit", ev_id)), ("🚫 取消約會", cb("a", "cal_cancel", ev_id))],
                        back_row(("🗓 日程", cb("v", "cal")))])


# ---------------------------------------------------------------- 設定
def settings_view(svc: Any) -> Reply:
    db, config = svc.db, svc.config
    q = quiet_hours(db, config)
    ck = checkin_times(db, config)
    ov = overlay(db)
    mark = lambda k: "（已改）" if k in ov else ""
    knobs = db.kv_get("knobs", {}) or {}
    lines = ["⚙️ 設定：",
             f"• 靜音時段：{q['start']} 至 {q['end']}{mark('quiet_start') or mark('quiet_end')}"]
    for k in ("morning", "afternoon", "evening"):
        lines.append(f"• {CHECKIN_NAMES[k]}：{ck.get(k, '—')}{mark(k)}")
    lines += [f"• 阿寶靜音：{'係' if knobs.get('muted') else '唔係'}",
              f"• Flash 思考：{knobs.get('flash_thinking') or '預設'} · Sonnet 思考：{knobs.get('sonnet_thinking') or '預設'}"]
    kb = [[("🌙 靜音時段", cb("f", "quiet"))],
          [("☀️ 朝早問候", cb("f", "ck_morning")), ("🌤 下晝問候", cb("f", "ck_afternoon"))],
          [("🌆 夜晚問候", cb("f", "ck_evening"))],
          [("🧠 思考程度", cb("v", "knobs"))], back_row()]
    return Reply("\n".join(lines), kb)


def knobs_view(svc: Any) -> Reply:
    knobs = svc.db.kv_get("knobs", {}) or {}
    text = (f"🧠 思考程度（下一句／下一個任務起生效）\nFlash：{knobs.get('flash_thinking') or '預設'}\n"
            f"Sonnet：{knobs.get('sonnet_thinking') or '預設'}")
    kb = [[(f"Flash {lv}", cb("a", "flash", lv)) for lv in ("off", "low", "high", "max")],
          [(f"Sonnet {lv}", cb("a", "sonnet", lv)) for lv in ("off", "low", "medium")],
          [(f"Sonnet {lv}", cb("a", "sonnet", lv)) for lv in ("high", "xhigh", "max")], back_row()]
    return Reply(text, kb)


# ---------------------------------------------------------------- 聯絡人
ROLES = {"family": "家人", "emergency_centre_sim": "緊急中心（模擬）", "centre": "中心"}


def contacts_view(svc: Any) -> Reply:
    db = svc.db
    lines = ["👥 聯絡人（跌倒時按次序致電家人；阿寶永遠唔會打 999）："]
    btns = []
    order = 0
    for c in db.query("SELECT * FROM contacts ORDER BY CASE role WHEN 'family' THEN 0 ELSE 1 END, priority, id"):
        if c["role"] == "family":
            order += 1
            head = f"{order}. "
        else:
            head = "• "
        ver = "已核實 ✅" if int(c.get("verified") or 0) else "未核實 ⚠️"
        tg = "有 Telegram" if c.get("telegram_chat_id") else "冇 Telegram"
        lines.append(f"{head}{c['name']}（{ROLES.get(c['role'], c['role'])}）電話 {mask(c.get('phone'))} {ver}，"
                     f"次序 {c['priority']}，{tg}")
        if c["role"] == "family":
            btns.append((f"✏️ {c['name']}", cb("v", "contact", c["id"])))
    lines.append("（只有家人會被自動致電；模擬緊急中心唔會。）")
    return Reply("\n".join(lines), rows(btns, 2) + [[("➕ 加聯絡人", cb("f", "contact_add"))], back_row()])


def contact_view(svc: Any, cid: str) -> Reply:
    c = svc.db.one("SELECT * FROM contacts WHERE id=?", (cid,))
    if c is None or c["role"] != "family":
        return Reply("搵唔到呢個聯絡人。", [back_row(("👥 聯絡人", cb("v", "contacts")))])
    text = (f"👤 {c['name']}（{ROLES.get(c['role'])}）\n電話：{mask(c.get('phone'))} "
            f"{'已核實 ✅' if int(c.get('verified') or 0) else '未核實 ⚠️'}\n致電次序：{c['priority']}\n"
            f"Telegram chat id：{c.get('telegram_chat_id') or '—'}")
    return Reply(text, [[("✏️ 修改", cb("f", "contact_edit", cid)), ("☎️ 核實電話", cb("a", "verify", cid))],
                        [("🗑 刪除", cb("a", "contact_del", cid)), ("👥 聯絡人", cb("v", "contacts"))], back_row()])


# ---------------------------------------------------------------- 最近通知
OUT_KINDS = {"ALERT": "🚨緊急", "NOTIFY": "✅完成", "INFO": "ℹ️通知", "DIGEST": "📋摘要", "HANDOFF": "🤝交接"}


def alerts_view(svc: Any) -> Reply:
    from .. import outbox
    items = outbox.outbox_list(svc.db, limit=10)
    lines = ["🔔 最近十個通知／致電："]
    for r in items:
        ts = (r.get("ts") or "")[5:16].replace("T", " ")
        if r["channel"] == "twilio":
            lines.append(f"• {ts} ☎️ 致電 {r['payload'].get('contact_name') or r.get('to_contact') or ''}"
                         f"（{r['status']}）")
        else:
            text = str(r["payload"].get("text") or "").replace("\n", " ")
            lines.append(f"• {ts} {OUT_KINDS.get(r['kind'], r['kind'])}（{r['status']}）{text[:60]}")
    if not items:
        lines.append("（未有）")
    return Reply("\n".join(lines), [back_row(("📅 今日狀況", cb("v", "today")))])


# ---------------------------------------------------------------- 系統健康
_PHONEISH = re.compile(r"\+?\d[\d \-]{6,}\d")


def _mask_detail(s: str) -> str:
    """Doctor details never carry keys (they are masked there); phone numbers are masked here too."""
    return _PHONEISH.sub(lambda m: mask(m.group(0)), s)


async def health_view(svc: Any) -> Reply:
    from ahbo.ops import doctor
    try:
        table = await doctor.rows(svc)
    except Exception as e:
        return Reply(f"系統健康檢查失敗：{type(e).__name__}", [back_row()])
    icon = {"green": "🟢", "red": "🔴", "fake": "⚪"}
    lines = ["🩺 系統健康："]
    for r in table:
        lines.append(f"{icon.get(r.get('status'), '•')} {r.get('item')}：{_mask_detail(str(r.get('detail') or ''))[:120]}")
    reds = sum(1 for r in table if r.get("status") == "red")
    lines.append(f"\n紅色 {reds} 項；⚪ = 示範（假）模式。")
    return Reply("\n".join(lines), [[("🔄 再檢查", cb("v", "health"))], back_row()])


# ---------------------------------------------------------------- 示範控制
def demo_view(svc: Any) -> Reply:
    knobs = svc.db.kv_get("knobs", {}) or {}
    text = (f"🎬 示範控制（虛擬時間 {svc.clock.now().strftime('%H:%M')}）\n"
            "撳掣之後會問你確認先做。跌倒／求助會標明「操作員觸發」。")
    kb = [[("🔄 重設示範", cb("a", "reset")), ("🛑 全部停止", cb("a", "panic"))],
          [("🤕 模擬跌倒", cb("a", "fall")), ("🆘 模擬求助", cb("a", "sos"))],
          [(s, cb("a", "scen", s)) for s in ("S1", "S2", "S3")],
          [(s, cb("a", "scen", s)) for s in ("S4", "S4ok")],
          [("🕒 較時間", cb("f", "clock")), ("⏱ 返真實時間", cb("a", "clock_now"))],
          [("🔈 取消靜音" if knobs.get("muted") else "🔇 靜音阿寶", cb("a", "unmute" if knobs.get("muted") else "mute")),
           ("🗣 講一句", cb("f", "say"))],
          [("💊 即刻食藥提醒", cb("v", "dose_pick")), ("👋 即刻問候", cb("v", "ck_pick"))],
          [("🧠 思考程度", cb("v", "knobs"))], back_row()]
    return Reply(text, kb)


def dose_pick_view(svc: Any) -> Reply:
    slots = svc.db.query("SELECT slot_id, time FROM med_schedule WHERE active=1 ORDER BY time")
    btns = [(f"💊 {s['time']}", cb("a", "dose", s["slot_id"])) for s in slots]
    return Reply("揀邊個時段即刻提醒食藥：", rows(btns, 3) + [back_row(("🎬 示範控制", cb("v", "demo")))])


def checkin_pick_view(svc: Any) -> Reply:
    btns = [(CHECKIN_NAMES[k], cb("a", "ck", k)) for k in ("morning", "afternoon", "evening")]
    return Reply("揀邊個問候即刻做：", [btns, back_row(("🎬 示範控制", cb("v", "demo")))])
