"""External actions (§4.3.5, D12/D13) and deep tasks (§8.1, §24.8).

Action: PROPOSED ─ action.confirm_request (her exact words, stt_confidence ≥ 0.6) ─► APPROVED_BY_RESIDENT ─► RUNNING
(`action.run`) ─ action.status ─► DONE | NEEDS_HUMAN | FAILED (120 s without a result → FAILED). Ah Chun is always
told (NOTIFY / HANDOFF / INFO); DONE books the calendar event; an `action` card and a P2 inject 行動結果 tell Po-po.
A tool can only *request* (INV-1): `action.run` is emitted here, after her recorded yes.

Deep: `deep.start_request` → `deep.start` (one at a time, `caps.yaml deep_tasks` per day). `deep.result` → cards on
the face now + P2 inject 背景結果 (or queued for the next session); a `needs_confirmation` signup → a PROPOSED action.
Deep/web text is data (INV-7): only the validated fields are used and spoken text is sanitised.

Slice `state.actions`: {action_id: {id, kind, state, target, title, ...}}; persisted to `actions` / `deep_tasks`.
"""
from __future__ import annotations

import re
from typing import Any

from ahbo.core import numwords as nw
from ahbo.core.events import P2, Command, Event, cmd, ui
from ahbo.core.ids import new_id
from ahbo.core.jsonutil import dumps, loads

from .. import arbiter
from ..common import family_contact_ids, note
from ..state import KernelState

LOW_CONFIDENCE = 0.6
RUN_TIMEOUT_S = 120.0
DEEP_GUARD_S = 120.0
NEGATIVE = ("唔好", "唔要", "唔使", "唔想", "唔去", "不要", "不用", "算啦", "算吧", "冇興趣")
TERMINAL = ("done", "needs_human", "failed")
METHOD = {"web_form": "playwright_form", "telegram_contact": "telegram_message"}
LINES = {"done": "報咗名喇，{when}嘅{title}，到時我會提你㗎。",
         "needs_human": "我已經叫阿珍幫手完成報名。",
         "failed": "報唔到名，我叫阿珍幫你睇下。"}


def _activity(ctx: Any, activity_id: str | None) -> dict[str, Any] | None:
    try:
        from ahbo.actions.activities import find_activity
        return find_activity(activity_id, ctx.settings.public_base_url)
    except Exception:
        return None


def _save(ctx: Any, a: dict[str, Any]) -> None:
    ctx.db.execute(
        "INSERT INTO actions(id,kind,target,method,state,details,question,approval,screenshots,result,created_at,"
        "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state, "
        "approval=excluded.approval, screenshots=excluded.screenshots, result=excluded.result, "
        "updated_at=excluded.updated_at",
        (a["id"], a["kind"], a.get("target"), a.get("method"), a["state"], dumps(a.get("details") or {}),
         a.get("question"), dumps(a["approval"]) if a.get("approval") else None, dumps(a.get("screenshots") or []),
         dumps(a["result"]) if a.get("result") is not None else None, a["created_at"], ctx.now.isoformat()))


def _when_words(hhmm: str | None) -> str:
    return nw.hhmm_words(hhmm, period=True) if hhmm and re.fullmatch(r"\d{1,2}:\d{2}", hhmm) else ""


def propose(state: KernelState, ctx: Any, kind: str, target: str | None, details: dict[str, Any],
            session_id: str | None, source: str = "agent") -> dict[str, Any]:
    details = dict(details or {})
    act = _activity(ctx, details.get("activity_id") or target) if kind == "signup" else None
    title = (act or {}).get("title") or details.get("title") or str(target or "")
    hhmm = (act or {}).get("time") or details.get("time")
    when = _when_words(hhmm)
    if kind == "signup":
        signup = (act or {}).get("signup") or {}
        method = METHOD.get(signup.get("method") or details.get("method") or "", "handoff")
        details.update({"activity_id": (act or {}).get("id") or details.get("activity_id") or target,
                        "title": title, "time": hhmm, "venue": (act or {}).get("venue"),
                        "venue_short": (act or {}).get("venue_short"), "url": details.get("url") or signup.get("url"),   # an explicitly passed url wins: domain checks judge the REAL target (fail-safe order)
                        "signup_method": signup.get("method")})
        question = f"我幫你報名{when}嘅{title}，好唔好呀？" if when else f"我幫你報名{title}，好唔好呀？"
    else:
        method = "telegram_message"
        question = f"我幫你傳個訊息俾{title}，好唔好呀？"
    a = {"id": new_id("act"), "kind": kind, "target": target, "method": method, "state": "proposed",
         "details": details, "question": question, "approval": None, "screenshots": [], "result": None,
         "title": title, "time": hhmm, "session_id": session_id, "source": source, "created_at": ctx.now.isoformat()}
    state.actions[a["id"]] = a
    _save(ctx, a)
    return a


def _confirm(state: KernelState, ctx: Any, ev: Event) -> tuple[dict[str, Any], list[Command]]:
    p = ev.payload
    a = state.actions.get(str(p.get("action_id") or ""))
    if a is None or a["state"] != "proposed":
        return {"ok": False, "reason": "not_proposed"}, []
    utter = str(p.get("utterance") or "").strip()
    conf = p.get("stt_confidence")
    if conf is not None and float(conf) < LOW_CONFIDENCE:
        return {"ok": False, "reason": "low_confidence"}, []
    if not utter or any(n in utter for n in NEGATIVE):
        return {"ok": False, "reason": "not_affirmative"}, []
    a["approval"] = {"utterance": utter, "stt_confidence": conf, "at": ctx.now.isoformat(), "event_id": ev.id}
    a["state"] = "approved_by_resident"
    _save(ctx, a)
    a["state"] = "running"
    _save(ctx, a)
    return {"ok": True, "status": "running", "action_id": a["id"]}, [
        cmd("action.run", P2, a["id"], action_id=a["id"]),
        cmd("timer.set", P2, a["id"], key=f"action:timeout:{a['id']}", delay_s=RUN_TIMEOUT_S,
            data={"action_id": a["id"]})]


def _ref(detail: Any) -> str | None:
    if isinstance(detail, dict):
        for k in ("reference", "ref", "booking_ref", "confirmation"):
            if detail.get(k):
                return str(detail[k])[:24]
        return None
    m = re.search(r"[A-Z]{1,4}-?\d{3,8}", str(detail or ""))
    return m.group(0) if m else None


# Plain Traditional Chinese for 阿珍 (QA 25 D6): never a raw dict, exception or English code in a family message.
REASON_WORDS = {"timeout": "等太耐冇回應", "browser_error": "報名網頁開唔到", "browser_unavailable": "報名網頁開唔到",
                "internal_error": "系統出咗問題", "daily_cap": "今日自動報名次數已經用晒", "captcha": "網頁要驗證碼",
                "forbidden_field": "網頁要填私人資料（例如身份證號碼）", "login": "網頁要登入", "payment": "網頁要付款",
                "unknown_required_field": "有必填資料阿寶唔知點填", "submit_rejected": "網頁唔接受個報名",
                "cross_domain_redirect": "網頁轉咗去第二個網站", "domain_not_allowed": "網址唔喺允許清單",
                "read_only_domain": "呢個網站只可以睇唔可以報名", "method_handoff": "呢個活動要人手報名",
                "unknown_method": "呢個活動要人手報名", "no_url": "搵唔到報名網址",
                "contact_not_allowlisted": "收件人唔喺聯絡人名單", "not_templated": "訊息格式唔啱",
                "already_sent": "訊息已經傳過", "wrong_method": "呢個行動唔可以咁做", "not_running": "行動已經停咗"}


def _reason_words(detail: Any) -> str:
    code = ""
    if isinstance(detail, dict):
        code = str(detail.get("reason") or detail.get("status") or "")
    elif isinstance(detail, str):
        code = detail.strip()
    return REASON_WORDS.get(code, "出現技術問題")


def _prefill_lines(prefill: Any) -> str:
    """Prefilled details as readable `key: value` lines; empty → ""."""
    if not isinstance(prefill, dict):
        return ""
    lines = []
    for k, v in prefill.items():
        if v is None or v == "" or isinstance(v, (dict, list)):
            continue
        lines.append(f"{str(k)[:30]}: {str(v)[:80]}")
    return "\n".join(lines[:12])


def _finish(state: KernelState, ctx: Any, a: dict[str, Any], status: str, detail: Any,
            screenshots: list[str]) -> list[Command]:
    a["state"] = status
    a["result"] = {"status": status, "detail": detail}
    if screenshots:
        a["screenshots"] = list(screenshots)
    _save(ctx, a)
    d = a.get("details") or {}
    title, hhmm = a.get("title") or "", a.get("time")
    venue = d.get("venue") or d.get("venue_short") or ""
    ref = _ref(detail)
    fam = family_contact_ids(ctx)
    out: list[Command] = [cmd("timer.cancel", P2, a["id"], key=f"action:timeout:{a['id']}")]
    msg = a["kind"] == "message"
    if status == "done":
        text = (f"已幫王婆婆傳咗訊息俾{title}。" if msg else
                f"已幫王婆婆報名{title}（{hhmm or ''} {venue}）。" + (f"參考編號：{ref}。" if ref else ""))
        out.append(cmd("family.send", P2, a["id"], kind="NOTIFY", contact_ids=fam, text=text,
                       screenshots=a.get("screenshots") or [], action_id=a["id"]))
        if a["kind"] == "signup" and hhmm:
            dt = ctx.now.replace(hour=int(hhmm[:2]), minute=int(hhmm[3:5]), second=0, microsecond=0).isoformat()
            row = ctx.db.one("SELECT id FROM calendar_events WHERE action_id=? OR (title=? AND datetime=?)",
                             (a["id"], title, dt))
            if row:
                ctx.db.execute("UPDATE calendar_events SET status='booked', action_id=? WHERE id=?", (a["id"], row["id"]))
            else:
                ctx.db.execute("INSERT INTO calendar_events(id,datetime,title,place,place_full,kind,status,source,action_id)"
                               " VALUES (?,?,?,?,?,?,?,?,?)", (new_id("cal"), dt, title, d.get("venue_short") or venue,
                                                              venue, "activity", "booked", "action", a["id"]))
        if msg:
            card = {"id": f"action_{a['id']}", "kind": "message", "label": "已傳訊息", "small": title[:14]}
            line = f"已經幫你傳咗訊息俾{title}。"
        else:
            card = {"id": f"action_{a['id']}", "kind": "action", "label": "已報名",
                    "small": f"編號 {ref}" if ref else title}
            w = _when_words(hhmm)
            line = LINES["done"].format(when=w, title=title) if w else f"報咗名喇，{title}到時我會提你㗎。"
    elif status == "needs_human":
        url = d.get("url") or ""
        prefill = _prefill_lines(d.get("prefill") or (detail.get("prefill") if isinstance(detail, dict) else None))
        text = (f"王婆婆想傳訊息俾{title}，阿寶未能自動完成，請你幫手聯絡。" if msg else
                f"王婆婆想報名{title}（{hhmm or ''} {venue}），阿寶未能自動完成（{_reason_words(detail)}），"
                f"請你幫手：{url}" + (f"\n已準備資料：\n{prefill}" if prefill else ""))
        out.append(cmd("family.send", P2, a["id"], kind="HANDOFF", contact_ids=fam, text=text,
                       screenshots=a.get("screenshots") or [], action_id=a["id"]))
        card = {"id": f"action_{a['id']}", "kind": "message", "label": "已通知阿珍",
                "small": "幫手傳訊息" if msg else "幫手報名"}
        line = "我已經叫阿珍幫手傳個訊息。" if msg else LINES["needs_human"]
    else:
        what = f"傳訊息俾{title}" if msg else f"報名{title}"
        out.append(cmd("family.send", P2, a["id"], kind="INFO", contact_ids=fam,
                       text=f"王婆婆想{what}，但係做唔到（{_reason_words(detail)}）。請你幫手睇下。",
                       action_id=a["id"]))
        card = {"id": f"action_{a['id']}", "kind": "message", "label": "已通知阿珍",
                "small": "傳唔到訊息" if msg else "報唔到名"}
        line = "傳唔到訊息，我叫阿珍幫你睇下。" if msg else LINES["failed"]
    out += _action_step_done(ctx, a, status)
    item = arbiter.make_item("行動結果", P2, line, "話俾佢知結果，一句就夠。", cards=[card], corr=a["id"])
    _, cmds = arbiter.request(state, ctx, item)
    return out + cmds


MSG_STEP_LABELS = {"done": "傳咗訊息", "needs_human": "叫咗阿珍幫手", "failed": "傳唔到訊息"}


def _action_step_done(ctx: Any, a: dict[str, Any], status: str) -> list[Command]:
    """The turn's confirm_action step 「幫你報緊名…」 stays active until the runner's result (the tool's outcome is
    `pending`); give it its ✓ / ✕ and label now if it is still showing (§23.6 rule 3, QA 25 D5)."""
    spec = (ctx.config.ui_steps.get("tools") or {}).get("confirm_action") or {}
    labels = MSG_STEP_LABELS if a.get("kind") == "message" else (spec.get("done") or {})
    for gid, g in ctx.ui.groups.items():
        for sid, s in g["steps"].items():
            if sid.startswith("confirm_action") and s.get("state") == "active":
                f = {"type": "step", "group": gid, "id": sid, "state": "failed" if status == "failed" else "done"}
                lab = labels.get(status) if isinstance(labels, dict) else None
                if lab:
                    f["label"] = lab
                return [ui(f, P2, a["id"])]
    return []


# ---------------------------------------------------------------- telegram-message actions
MESSAGE_ROLES = ("family", "centre")      # who a templated action message may go to (never a phone-only line)


def _message_send(state: KernelState, ctx: Any, ev: Event) -> list[Command] | None:
    """`action.message_send` (source `action`, from `ahbo.actions.telegram_msg`): the runner built the template
    text for an action she approved; the Kernel re-checks the action and the recipient against the `contacts`
    allowlist before the one `family.send` that actually goes out (INV-1, INV-9/§11)."""
    p = ev.payload
    a = state.actions.get(str(p.get("action_id") or ""))
    cid = str(p.get("contact_id") or "").strip()
    reason = None
    row = ctx.db.one("SELECT id, name, role, verified FROM contacts WHERE id=?", (cid,)) if cid else None
    text = str(p.get("text") or "").strip()
    if ev.source != "action":
        reason = "bad_source"
    elif a is None or a["state"] != "running" or not a.get("approval"):
        reason = "not_running"
    elif a.get("method") != "telegram_message":
        reason = "wrong_method"
    elif row is None or int(row.get("verified") or 0) != 1 or row.get("role") not in MESSAGE_ROLES:
        reason = "contact_not_allowlisted"
    elif not text or p.get("template") is not True:
        reason = "not_templated"
    elif a.get("message_sent"):
        reason = "already_sent"
    if reason:
        note(ctx, "action.message_refused", {"action_id": p.get("action_id"), "contact_id": cid, "reason": reason},
             ev.correlation_id or p.get("action_id"))
        if a is not None and a["state"] not in TERMINAL and not a.get("message_sent") and reason != "bad_source":
            a["message_refused"] = reason      # nothing went out: a later `done` from the runner → needs_human
        return None
    a["message_sent"] = row["id"]
    a.pop("message_refused", None)
    return [cmd("family.send", P2, a["id"], kind="NOTIFY", contact_ids=[row["id"]], text=text[:1000],
                action_id=a["id"])]


# ---------------------------------------------------------------- deep tasks
def _speakable(text: str) -> str:
    """Deep text is data (INV-7): keep only sentences without Latin letters/URLs and without instruction markers
    (QA 22 D2), at most two. URLs are cut with the spoken filter's punctuation-aware regex, so a URL glued to
    Chinese text never swallows the following sentence. A dropped sentence is dropped: there is no fallback."""
    from ahbo.agent.filters.spoken import INSTRUCTION_RE, URL_RE
    parts = re.split(r"(?<=[。！？])", URL_RE.sub("", str(text or "")))
    keep = [s.strip() for s in parts if s.strip() and not re.search(r"[A-Za-z0-9０-９]", s)
            and not INSTRUCTION_RE.search(s)]
    return "".join(keep[:2])


_DEEP_KEYS = {"task_id", "status", "spoken_summary", "card", "sources", "signup_methods", "needs_confirmation"}


def _schema_errors(res: dict[str, Any]) -> list[str]:
    try:
        from ahbo.deep.schema import validate_result
        return validate_result(res)
    except ImportError:
        extra = set(res) - _DEEP_KEYS
        missing = (_DEEP_KEYS - {"task_id"}) - set(res)
        return [f"extra:{sorted(extra)}"] * bool(extra) + [f"missing:{sorted(missing)}"] * bool(missing)


def _deep_start(state: KernelState, ctx: Any, ev: Event) -> tuple[dict[str, Any], list[Command]]:
    p = ev.payload
    running = ctx.db.one("SELECT id FROM deep_tasks WHERE status='running' LIMIT 1")
    if running:
        return {"ok": False, "status": "busy"}, []
    cap = int((ctx.config.caps or {}).get("deep_tasks", 15))
    n = ctx.db.one("SELECT COUNT(*) AS n FROM deep_tasks WHERE substr(started_at,1,10)=?",
                   (ctx.now.date().isoformat(),))["n"]
    if n >= cap:
        return {"ok": False, "status": "busy", "reason": "daily_cap"}, []
    tid = new_id("deep")
    sid = p.get("session_id") or (state.session.id if state.session else None)
    ctx.db.execute("INSERT INTO deep_tasks(id,goal,constraints,status,session_id,started_at) VALUES (?,?,?,?,?,?)",
                   (tid, str(p.get("goal") or "")[:500], str(p.get("constraints") or "")[:500], "running", sid,
                    ctx.now.isoformat()))
    if state.session is not None:
        state.session.deep_running = True
    return {"ok": True, "status": "started", "task_id": tid}, [
        cmd("deep.start", P2, tid, task_id=tid, goal=p.get("goal") or "", constraints=p.get("constraints") or "",
            session_id=sid),
        cmd("timer.set", P2, tid, key=f"deep:guard:{tid}", delay_s=DEEP_GUARD_S, data={"task_id": tid})]


def _deep_result(state: KernelState, ctx: Any, ev: Event) -> list[Command]:
    p = ev.payload
    tid = str(p.get("task_id") or "")
    res = p.get("result") if isinstance(p.get("result"), dict) else {}
    if _schema_errors(res):            # defence in depth (INV-7): an invalid result is never acted on
        res = {"status": "failed", "invalid": True}
    status = res.get("status") if res.get("status") in ("done", "failed", "timeout") else "failed"
    ctx.db.execute("INSERT INTO deep_tasks(id,status,started_at) VALUES (?,?,?) ON CONFLICT(id) DO NOTHING",
                   (tid, status, ctx.now.isoformat()))
    ctx.db.execute("UPDATE deep_tasks SET status=?, result=?, signup_methods=?, finished_at=? WHERE id=?",
                   (status, dumps(res), dumps(res.get("signup_methods") or []), ctx.now.isoformat(), tid))
    if state.session is not None:
        state.session.deep_running = False
    out: list[Command] = [cmd("timer.cancel", P2, tid, key=f"deep:guard:{tid}")]
    out += _deep_step_done(ctx, tid, status == "done")
    if status != "done":
        item = arbiter.make_item("背景結果", P2, "今次搵唔到合適嘅嘢，遲啲再幫你睇過。", "", corr=tid,
                                 mark={"deep_task": tid})
        _, cmds = arbiter.request(state, ctx, item)
        return out + cmds
    cards = []
    for i, it in enumerate(((res.get("card") or {}).get("items") or [])[:4]):
        if not isinstance(it, dict):
            continue
        t, act = str(it.get("time") or ""), _speakable(str(it.get("activity") or "")) or "活動"
        label = f"{nw.card_time(t)} {act}" if re.fullmatch(r"\d{1,2}:\d{2}", t) else act
        card = {"id": f"deep_{tid}_{i}", "kind": "activity", "label": label[:14]}
        place = _speakable(str(it.get("place") or ""))
        if place:
            card["small"] = place[:14]
        cards.append(card)
    for c in cards:
        out.append(ui({"type": "card", "card": c}, P2, tid))
    instruction = ""
    for nc in res.get("needs_confirmation") or []:
        if isinstance(nc, dict) and nc.get("action") == "signup":
            args = nc.get("args") if isinstance(nc.get("args"), dict) else {}
            a = propose(state, ctx, "signup", args.get("activity_id"), args, ctx_session(state), source="deep")
            instruction = f"之後問佢：{a['question']}（action_id {a['id']}）"
            break
    summary = _speakable(res.get("spoken_summary") or "") or "幫你搵到啲嘢，睇吓個畫面。"
    item = arbiter.make_item("背景結果", P2, summary, instruction, corr=tid, mark={"deep_task": tid})
    _, cmds = arbiter.request(state, ctx, item)
    return out + cmds


def ctx_session(state: KernelState) -> str | None:
    return state.session.id if state.session else None


def _deep_step_done(ctx: Any, tid: str, ok: bool) -> list[Command]:
    """The turn's deep_task step stays active until the result (ui_steps.yaml); close it if it is still showing."""
    spec = (ctx.config.ui_steps.get("tools") or {}).get("deep_task") or {}
    for gid, g in ctx.ui.groups.items():
        for sid, s in g["steps"].items():
            if sid.startswith("deep_task") and s.get("state") == "active":
                f = {"type": "step", "group": gid, "id": sid, "state": "done" if ok else "failed"}
                lab = ((spec.get("done") or {}).get("ok") if ok else spec.get("failed"))
                if lab:
                    f["label"] = lab
                return [ui(f, P2, tid)]
    return []


# ---------------------------------------------------------------- reducer
def reduce(state: KernelState, ev: Event, ctx: Any) -> list[Command] | None:
    t, p = ev.type, ev.payload
    if t == "action.propose_request":
        kind = p.get("kind") if p.get("kind") in ("signup", "message") else None
        if kind is None:
            ctx.reply(ev, {"ok": False, "reason": "bad_args"})
            return None
        a = propose(state, ctx, kind, p.get("target"), p.get("details") or {}, p.get("session_id"))
        ctx.reply(ev, {"ok": True, "action_id": a["id"], "question": a["question"]})
        return None
    if t == "action.confirm_request":
        res, out = _confirm(state, ctx, ev)
        ctx.reply(ev, res)
        return out or None
    if t == "action.status":
        a = state.actions.get(str(p.get("action_id") or ""))
        if a is None or a["state"] in TERMINAL:
            return None
        status = str(p.get("status") or "")
        if status == "done" and a.get("message_refused"):     # the runner thinks it sent; the Kernel refused
            status, p = "needs_human", {**p, "detail": {"reason": a["message_refused"]}}
        if status in TERMINAL:
            return _finish(state, ctx, a, status, p.get("detail"), list(p.get("screenshots") or []))
        if status in ("planned", "filling", "submitted"):
            a["progress"] = status
            if p.get("screenshots"):
                a["screenshots"] = list(p["screenshots"])
            _save(ctx, a)
        return None
    if t == "timer.fired":
        key, data = p.get("key", ""), p.get("data") or {}
        if key.startswith("action:timeout:"):
            a = state.actions.get(str(data.get("action_id") or ""))
            if a and a["state"] == "running":
                return _finish(state, ctx, a, "failed", "timeout", [])
        if key.startswith("deep:guard:"):
            tid = data.get("task_id")
            row = ctx.db.one("SELECT status FROM deep_tasks WHERE id=?", (tid,))
            if row and row["status"] == "running":
                ctx.db.execute("UPDATE deep_tasks SET status='timeout', finished_at=? WHERE id=?",
                               (ctx.now.isoformat(), tid))
                if state.session is not None:
                    state.session.deep_running = False
                return _deep_step_done(ctx, tid, False) or None
        return None
    if t == "action.message_send":
        return _message_send(state, ctx, ev)
    if t == "deep.start_request":
        res, out = _deep_start(state, ctx, ev)
        ctx.reply(ev, res)
        return out or None
    if t == "deep.result":
        return _deep_result(state, ctx, ev)
    if t == "operator.reset":
        state.actions = {}
    return None


def _loaded(kernel: Any) -> None:
    if kernel.state.actions:
        return
    for r in kernel.db.query("SELECT * FROM actions WHERE state NOT IN ('done','needs_human','failed')"):
        d = loads(r.get("details"), {}) or {}
        kernel.state.actions[r["id"]] = {
            "id": r["id"], "kind": r["kind"], "target": r.get("target"), "method": r.get("method"),
            "state": r["state"], "details": d, "question": r.get("question"),
            "approval": loads(r.get("approval"), None), "screenshots": loads(r.get("screenshots"), []) or [],
            "result": loads(r.get("result"), None), "title": d.get("title") or r.get("target"), "time": d.get("time"),
            "session_id": None, "source": "db", "created_at": r.get("created_at") or kernel.clock.iso()}
        if r["state"] == "running":      # the runner's result may never come after a restart: keep the 120 s guard
            try:
                kernel._set_timer(f"action:timeout:{r['id']}", RUN_TIMEOUT_S, {"action_id": r["id"]}, r["id"])
            except RuntimeError:
                pass


def setup(kernel: Any) -> None:
    if _loaded not in kernel.loaded_hooks:
        kernel.loaded_hooks.append(_loaded)
