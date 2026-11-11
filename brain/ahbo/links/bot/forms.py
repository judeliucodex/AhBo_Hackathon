"""Multi-step edit forms (one question per step, typed or by button), ending in a 「確認／取消」 step.

A form is a list of `Field`s; the answers build one change (`changes.describe/apply`). State lives in the per-chat
conversation (`state.py`), so a restart doesn't lose it. Text that Po-po will hear or see (medicine names, calendar
titles/places/routes, operator lines) is converted to Traditional characters and may not contain English words
(INV-10); the medicine's English name is for 阿珍 only and is never spoken.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from ahbo.core.jsonutil import loads
from ahbo.settings_store import effective

from . import phones
from .changes import BotError
from .ui import Reply, cb, rows
from .views import CHECKIN_NAMES, KINDS, PILLS, catalogue, pill_label, slot_names

KEEP, SKIP, DONE = "_keep", "_skip", "_done"
PILL_TOKENS = tuple(PILLS)
PILL_CHOICES = [f"{a[-1]}{b[-1]}" for a in PILL_TOKENS for b in PILL_TOKENS]       # "aa", "ab", … "cc"
CAL_KINDS = ("medical", "meal", "plan", "other")
ANCHORS = ("S1", "S2", "S3", "S4")


@dataclass(frozen=True)
class Field:
    key: str
    prompt: str
    kind: str                  # text time date choice multi pills phone int chat clock
    optional: bool = False
    maxlen: int = 30
    spoken: bool = False       # Po-po hears/sees it: Traditional only, no English words


# ================================================================ parsing
def _nf(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "").strip()


_TIME = re.compile(r"^(朝早|上晝|早上|中午|下晝|晏晝|下午|夜晚|晚上|晚)?\s*(\d{1,2})(?:[:.點时時]\s*(\d{1,2}|半)?)?\s*(?:分)?$")


def parse_time(raw: str) -> str:
    s = _nf(raw).replace(" ", "")
    m4 = re.fullmatch(r"(\d{1,2})(\d{2})", s)
    if m4:
        h, mi = int(m4.group(1)), int(m4.group(2))
    else:
        m = _TIME.match(s)
        if not m:
            raise BotError("時間格式唔啱，例如 08:30 或者 下晝3點半。")
        h = int(m.group(2))
        mi = 30 if m.group(3) == "半" else int(m.group(3) or 0)
        if m.group(1) in ("下晝", "晏晝", "下午", "夜晚", "晚上", "晚") and h < 12:
            h += 12
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise BotError("時間唔啱（00:00 至 23:59）。")
    return f"{h:02d}:{mi:02d}"


def parse_date(raw: str, today: date) -> str:
    s = _nf(raw).replace(" ", "")
    rel = {"今日": 0, "今天": 0, "聽日": 1, "明日": 1, "明天": 1, "後日": 2, "后日": 2}
    if s in rel:
        return (today + timedelta(days=rel[s])).isoformat()
    try:
        if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", s):
            y, mo, d = (int(x) for x in s.split("-"))
            out = date(y, mo, d)
        else:
            m = re.fullmatch(r"(\d{1,2})(?:[-/月])(\d{1,2})日?", s)
            if not m:
                raise ValueError
            out = date(today.year, int(m.group(1)), int(m.group(2)))
            if out < today:
                out = date(today.year + 1, out.month, out.day)
    except ValueError:
        raise BotError("日期唔啱，例如 今日、聽日、11-25 或者 2026-11-25。") from None
    if out < today:
        raise BotError("唔可以揀已經過咗嘅日子。")
    if out > today + timedelta(days=366):
        raise BotError("日期太遠喇（一年內）。")
    return out.isoformat()


def parse_text(raw: str, f: Field) -> str:
    s = " ".join(str(raw or "").split())
    if not s:
        raise BotError("唔可以留空。")
    if len(s) > f.maxlen:
        raise BotError(f"太長喇（最多 {f.maxlen} 個字）。")
    if f.spoken:
        from ahbo.agent.filters.spoken import to_traditional
        s = to_traditional(s)
        if re.search(r"[A-Za-z]{2,}", s):
            raise BotError("婆婆會聽到／見到呢句，請用中文寫，唔好用英文字。")
    return s


def parse_int(raw: str) -> int:
    s = _nf(raw)
    if not re.fullmatch(r"\d{1,2}", s) or not 1 <= int(s) <= 9:
        raise BotError("請輸入 1 至 9（1 最先打）。")
    return int(s)


def parse_chat(raw: str) -> str:
    s = _nf(raw).replace(" ", "")
    if not re.fullmatch(r"-?\d{5,20}", s):
        raise BotError("chat id 係一串數字（佢可以喺機械人度打 /whoami 攞）。")
    return s


def parse_clock(raw: str, svc: Any) -> dict[str, Any]:
    s = _nf(raw).replace(" ", "")
    if s.lower() in ("now", "而家", "真實", "real"):
        return {"op": "clock_now", "args": {}}
    if s in ANCHORS or s == "S4ok":
        return {"op": "clock", "args": {"body": {"anchor": s}, "label": f"{s} 開始時間"}}
    m = re.fullmatch(r"([+-])(\d{1,4})", s)
    if m:
        mins = int(m.group(2)) * (1 if m.group(1) == "+" else -1)
        off = float(svc.clock.offset_s) + mins * 60
        return {"op": "clock", "args": {"body": {"offset_s": off}, "label": f"{'+' if mins >= 0 else ''}{mins} 分鐘"}}
    hhmm = parse_time(s)
    return {"op": "clock", "args": {"body": {"set": hhmm}, "label": hhmm}}


# ================================================================ form definitions
def _med_current(svc: Any, mid: str | None) -> dict[str, Any] | None:
    r = svc.db.one("SELECT * FROM med_catalogue WHERE id=?", (mid,)) if mid else None
    if r is None:
        return None
    pill = loads(r["pill"], [])
    return {"yue": r["yue"], "en": r.get("en"), "dose": r.get("dose"),
            "pill": "".join(t[-1] for t in pill) if len(pill) == 2 else None}


def _slot_current(svc: Any, sid: str | None) -> dict[str, Any] | None:
    r = svc.db.one("SELECT * FROM med_schedule WHERE slot_id=?", (sid,)) if sid else None
    return {"time": r["time"], "meds": loads(r["med_names"], [])} if r else None


def _cal_current(svc: Any, eid: str | None) -> dict[str, Any] | None:
    r = svc.db.one("SELECT * FROM calendar_events WHERE id=?", (eid,)) if eid else None
    if r is None:
        return None
    return {"date": r["datetime"][:10], "time": r["datetime"][11:16], "title": r["title"], "place": r.get("place"),
            "kind": r["kind"] if r["kind"] in CAL_KINDS else "other", "route": r.get("route")}


def _contact_current(svc: Any, cid: str | None) -> dict[str, Any] | None:
    r = svc.db.one("SELECT * FROM contacts WHERE id=? AND role='family'", (cid,)) if cid else None
    if r is None:
        return None
    return {"name": r["name"], "phone": r.get("phone"), "priority": r["priority"], "chat": r.get("telegram_chat_id")}


def _settings_current(keys: tuple[str, ...]) -> Callable[[Any, str | None], dict[str, Any]]:
    def cur(svc: Any, _id: str | None) -> dict[str, Any]:
        e = effective(svc.db, svc.config)
        return {k: e.get(k) for k in keys}
    return cur


MED_FIELDS = [Field("yue", "藥物廣東話名（婆婆會聽到），例如 血壓藥：", "text", maxlen=12, spoken=True),
              Field("en", "英文藥名（只係俾你睇），例如 Amlodipine：", "text", optional=True, maxlen=40),
              Field("dose", "份量，例如 5 mg：", "text", optional=True, maxlen=20),
              Field("pill", "藥丸顏色（兩邊）：", "pills")]
SLOT_FIELDS = [Field("time", "食藥時間，例如 08:00：", "time"),
               Field("meds", "揀呢個時間要食嘅藥（可以揀幾隻），揀完撳「完成」：", "multi")]
CAL_FIELDS = [Field("date", "日期（今日／聽日／11-25）：", "date"),
              Field("time", "時間，例如 10:30：", "time"),
              Field("title", "做咩（婆婆會聽到），例如 覆診：", "text", maxlen=20, spoken=True),
              Field("place", "地點，例如 聯合醫院：", "text", optional=True, maxlen=20, spoken=True),
              Field("kind", "類別：", "choice"),
              Field("route", "點去（巴士路線等），例如 樓下巴士站搭巴士去聯合醫院：", "text", optional=True, maxlen=60,
                    spoken=True)]
CONTACT_FIELDS = [Field("name", "名，例如 阿明：", "text", maxlen=20),
                  Field("phone", "電話號碼（香港 8 個字或者 +國家碼），跌倒時阿寶會打：", "phone", optional=True),
                  Field("priority", "致電次序（1 最先打）：", "int"),
                  Field("chat", "Telegram chat id（佢喺機械人打 /whoami 攞；有咗就可以用呢個機械人）：", "chat",
                        optional=True)]


@dataclass(frozen=True)
class FormDef:
    title: str
    fields: list[Field]
    current: Callable[[Any, str | None], dict[str, Any] | None]
    build: Callable[[Any, str | None, dict[str, Any]], tuple[str, dict[str, Any]]]
    needs_id: bool = False


def _pill_list(code: str) -> list[str]:
    return [f"--pill-{code[0]}", f"--pill-{code[1]}"]


FORMS: dict[str, FormDef] = {
    "med_add": FormDef("加藥物", MED_FIELDS, lambda s, i: None,
                       lambda s, i, d: ("med_upsert", {"id": None, "yue": d["yue"], "en": d.get("en"),
                                                       "dose": d.get("dose"), "pill": _pill_list(d["pill"])})),
    "med_edit": FormDef("改藥物", MED_FIELDS, _med_current,
                        lambda s, i, d: ("med_upsert", {"id": i, "yue": d["yue"], "en": d.get("en"),
                                                        "dose": d.get("dose"), "pill": _pill_list(d["pill"])}),
                        needs_id=True),
    "slot_add": FormDef("加食藥時間", SLOT_FIELDS, lambda s, i: None,
                        lambda s, i, d: ("slot_upsert", {"slot_id": None, "time": d["time"], "meds": d["meds"]})),
    "slot_edit": FormDef("改食藥時間", SLOT_FIELDS, _slot_current,
                         lambda s, i, d: ("slot_upsert", {"slot_id": i, "time": d["time"], "meds": d["meds"]}),
                         needs_id=True),
    "cal_add": FormDef("加約會", CAL_FIELDS, lambda s, i: None,
                       lambda s, i, d: ("cal_upsert", {"id": None, **{f.key: d.get(f.key) for f in CAL_FIELDS}})),
    "cal_edit": FormDef("改約會", CAL_FIELDS, _cal_current,
                        lambda s, i, d: ("cal_upsert", {"id": i, **{f.key: d.get(f.key) for f in CAL_FIELDS}}),
                        needs_id=True),
    "quiet": FormDef("靜音時段", [Field("quiet_start", "靜音開始時間（夜晚），例如 22:30：", "time"),
                                Field("quiet_end", "靜音結束時間（朝早），例如 07:00：", "time")],
                     _settings_current(("quiet_start", "quiet_end")),
                     lambda s, i, d: ("settings", {"patch": {"quiet_start": d["quiet_start"],
                                                             "quiet_end": d["quiet_end"]}})),
    **{f"ck_{k}": FormDef(CHECKIN_NAMES[k], [Field(k, f"{CHECKIN_NAMES[k]}時間，例如 08:30：", "time")],
                          _settings_current((k,)),
                          lambda s, i, d, k=k: ("settings", {"patch": {k: d[k]}}))
       for k in ("morning", "afternoon", "evening", "digest")},
    "contact_add": FormDef("加聯絡人", CONTACT_FIELDS, lambda s, i: None,
                           lambda s, i, d: ("contact_upsert", {"id": None, **{f.key: d.get(f.key)
                                                                               for f in CONTACT_FIELDS}})),
    "contact_edit": FormDef("改聯絡人", CONTACT_FIELDS, _contact_current,
                            lambda s, i, d: ("contact_upsert", {"id": i, **{f.key: d.get(f.key)
                                                                            for f in CONTACT_FIELDS}}),
                            needs_id=True),
    "clock": FormDef("較時間", [Field("clock", "虛擬時間：打 07:58、+30（分鐘）、now，或者揀場景開始時間：", "clock")],
                     lambda s, i: None, lambda s, i, d: (d["clock"]["op"], d["clock"]["args"])),
    "say": FormDef("阿寶講一句", [Field("text", "打低要阿寶講嘅說話（會標明操作員）：", "text", maxlen=120,
                                    spoken=True)],
                   lambda s, i: None, lambda s, i, d: ("say", {"text": d["text"]})),
}


# ================================================================ rendering a step
def show_value(svc: Any, f: Field, v: Any) -> str:
    if v in (None, "", []):
        return "（空）"
    if f.kind == "multi":
        return slot_names(svc.db, list(v))
    if f.kind == "pills":
        return pill_label(_pill_list(v)) if isinstance(v, str) and len(v) == 2 else "—"
    if f.kind == "phone":
        return phones.mask(v)
    if f.kind == "choice":
        return KINDS.get(v, str(v))
    return str(v)


def step_reply(svc: Any, form: dict[str, Any], note: str = "") -> Reply:
    fd = FORMS[form["name"]]
    f = fd.fields[form["step"]]
    data = form["data"]
    editing = form.get("editing", False)
    lines = [note] if note else []
    lines.append(f"【{fd.title}】第 {form['step'] + 1}/{len(fd.fields)} 步")
    if editing:
        lines.append(f"而家：{show_value(svc, f, form['orig'].get(f.key))}")
    lines.append(f.prompt)
    btns: list[tuple[str, str]] = []
    if f.kind == "choice":
        btns += [(KINDS[k], cb("i", k)) for k in CAL_KINDS]
    elif f.kind == "pills":
        btns += [(pill_label(_pill_list(c)), cb("i", c)) for c in PILL_CHOICES]
    elif f.kind == "date":
        btns += [(t, cb("i", t)) for t in ("今日", "聽日", "後日")]
    elif f.kind == "clock":
        btns += [(f"{a} 開始", cb("i", a)) for a in ANCHORS] + [("真實時間", cb("i", "now"))]
    elif f.kind == "multi":
        chosen = set(data.get(f.key) or [])
        btns += [(("☑️ " if mid in chosen else "⬜ ") + m["yue"], cb("i", mid)) for mid, m in catalogue(svc.db).items()]
        if chosen:
            lines.append(f"已揀：{slot_names(svc.db, [m for m in catalogue(svc.db) if m in chosen])}")
    kb = rows(btns, 3 if f.kind in ("pills", "multi", "date") else 2)
    tail: list[tuple[str, str]] = []
    if f.kind == "multi":
        tail.append(("✔️ 完成", cb("i", DONE)))
    if editing:
        tail.append(("保持不變", cb("i", KEEP)))
    if f.optional:
        tail.append(("留空" if editing else "略過", cb("i", SKIP)))
    tail.append(("❌ 取消", cb("x")))
    kb.append(tail)
    return Reply("\n".join(lines), kb)


def start(svc: Any, name: str, item_id: str | None) -> tuple[dict[str, Any] | None, Reply | None]:
    fd = FORMS.get(name)
    if fd is None:
        return None, Reply("唔知呢個表格。")
    cur = fd.current(svc, item_id)
    if fd.needs_id and cur is None:
        return None, Reply("搵唔到要改嘅嘢（可能已經刪除咗）。")
    editing = fd.needs_id or name in ("quiet",) or name.startswith("ck_")
    form = {"name": name, "id": item_id, "step": 0, "data": dict(cur or {}), "orig": dict(cur or {}),
            "editing": editing}
    if fd.fields[0].kind == "multi" and not editing:
        form["data"].setdefault(fd.fields[0].key, [])
    return form, None


def _parse(svc: Any, f: Field, raw: str, data: dict[str, Any]) -> Any:
    if f.kind == "time":
        return parse_time(raw)
    if f.kind == "date":
        return parse_date(raw, svc.clock.today())
    if f.kind == "int":
        return parse_int(raw)
    if f.kind == "chat":
        return parse_chat(raw)
    if f.kind == "clock":
        return parse_clock(raw, svc)
    if f.kind == "phone":
        e164, reason = phones.normalise(raw)
        if e164 is None:
            raise BotError(phones.reason_text(reason))
        return e164
    if f.kind == "choice":
        s = _nf(raw)
        for k, label in KINDS.items():
            if s in (k, label) and k in CAL_KINDS:
                return k
        raise BotError("請撳下面其中一個類別。")
    if f.kind == "pills":
        s = _nf(raw)
        if s in PILL_CHOICES:
            return s
        raise BotError("請撳下面其中一個顏色。")
    return parse_text(raw, f)


def answer(svc: Any, form: dict[str, Any], raw: str, *, button: bool) -> tuple[str, Any]:
    """Feed one answer. Returns ("next", Reply) | ("again", Reply) | ("done", (op, args))."""
    fd = FORMS[form["name"]]
    f = fd.fields[form["step"]]
    data = form["data"]
    val = str(raw or "").strip()
    typed_alias = {"略過": SKIP, "跳過": SKIP, "-": SKIP, "保持": KEEP, "不變": KEEP, "保持不變": KEEP,
                   "完成": DONE, "done": DONE}
    if not button:
        val = typed_alias.get(val, val)
    try:
        if val == KEEP:
            if not form.get("editing"):
                raise BotError("呢度冇舊資料可以保持。")
            data[f.key] = form["orig"].get(f.key)
            if data[f.key] in (None, "", []) and not f.optional:
                raise BotError("呢一項唔可以留空。")
        elif val == SKIP:
            if not f.optional:
                raise BotError("呢一項唔可以略過。")
            data[f.key] = None
        elif f.kind == "multi":
            chosen = list(data.get(f.key) or [])
            cat = catalogue(svc.db)
            if val == DONE:
                if not chosen:
                    raise BotError("最少揀一隻藥。")
            else:
                picks = [val] if val in cat else [mid for mid, m in cat.items()
                                                  if m["yue"] in re.split(r"[\s、,，]+", val)]
                if not picks:
                    raise BotError("搵唔到呢隻藥，請撳下面嘅掣。")
                for p in picks:
                    if p in chosen:
                        chosen.remove(p)
                    else:
                        chosen.append(p)
                data[f.key] = [m for m in cat if m in chosen]          # catalogue order
                return "again", step_reply(svc, form)
        else:
            data[f.key] = _parse(svc, f, val, data)
    except BotError as e:
        return "again", step_reply(svc, form, note=f"⚠️ {e}")
    form["step"] += 1
    if form["step"] < len(fd.fields):
        return "next", step_reply(svc, form)
    try:
        return "done", fd.build(svc, form.get("id"), data)
    except (KeyError, TypeError):
        form["step"] = 0
        return "again", step_reply(svc, form, note="⚠️ 資料唔齊，請由頭再嚟。")
