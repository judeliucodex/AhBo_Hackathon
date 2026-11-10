"""Fall incident (§4.3.1, S4): the most important flow. Deterministic; models are advisory only (INV-2, INV-3).

    IDLE ─ sensor.fall_suspected | sensor.sos_pressed | operator.fall ─► ALARM
    ALARM ─ F1 done (or its max) ─► PHOTO_CHECK ─ F2 done (+ ≤ 0.8 s for a pending vision note) ─► AWAITING_OK
    agent.concern(emergency) / red flag ─► AWAITING_OK directly (no photo)
    AWAITING_OK ─ OK-matcher on her words | device.touch_ok (from the device) | operator.ok (from the operator) ─► RESOLVED_OK
                ─ help-matcher | countdown timer | operator.escalate ─► ESCALATING
    RESOLVED_OK ─ F5 done ─► (mood idle, steps clear) ─► CLOSED
    ESCALATING ─ F4 done ─► REASSURING ─ family.button(handled) | call acknowledged | operator.ok ─► ACKNOWLEDGED
    any ─ operator.resolve | family.button(resolved) ─► CLOSED
    no ack within T_ack (demo 2 min, prod 5 min) ─► call the next verified contact (≤ 3 calls per incident)

Face order follows the kit storyboards S4/S4ok (golden traces) and BUILD_LOG D-8 (ok_button + countdown after F3).
INV-2: an agent-sourced `operator.*` / `device.touch_ok` never resolves; vision notes never change state.
INV-9: calls only to `contacts` with verified=1 and role emergency_centre_sim|family, never an emergency number.

Slice `state.incident`: a dict (see `_new`), persisted to `incidents` on every change; rebuilt on restart.
"""
from __future__ import annotations

import asyncio
from typing import Any

from ahbo.core.events import P0, P1, Command, Event, cmd, ui
from ahbo.core.ids import new_id
from ahbo.core.jsonutil import dumps, loads

from .. import arbiter
from ..common import address, call_order, callable_contact, clip_max, family_contact_ids
from ..matchers import match_ok
from ..state import KernelState
from .session import close_session, open_session, set_mode

PRE = ("ALARM", "PHOTO_CHECK", "AWAITING_OK")
POST = ("ESCALATING", "REASSURING", "ACKNOWLEDGED")
COOLDOWN_S = 60.0
MAX_CALLS = 3
PHOTO_WAIT_S = 0.8
CLEAR_AFTER_F5_S = 1.5
GROUP = "fall"
CARD = "incident"
FALLBACK_VISION_NOTE = "可能有人跌咗喺地下"     # the PRD's fake vision result (§26.3)


def params(ctx: Any) -> dict[str, Any]:
    demo = ctx.settings.profile != "production"
    return {"countdown": 5 if demo else 30, "repeat_f3": None if demo else 15.0, "t_ack": 120.0 if demo else 300.0,
            "f4": "F4" if demo else "F4p"}


def inc_of(state: KernelState) -> dict[str, Any] | None:
    return state.incident if isinstance(state.incident, dict) else None


def vision_short(note: str | None) -> str | None:
    """Fixed template (§23.6 `vision_short_or_default`): only a concern-raising note is ever shown, ≤ 12 chars."""
    if not note:
        return None
    low = note.lower()
    if any(w in note for w in ("跌", "地下", "地上", "瞓喺", "躺")) or \
            any(w in low for w in ("on the floor", "lying", "fallen", "on the ground")):
        return "可能有人跌咗喺地下"
    return None


def _steps_cfg(ctx: Any) -> dict[str, Any]:
    return ctx.config.ui_steps.get("fall") or {}


# ---------------------------------------------------------------- persistence
def _save(ctx: Any, inc: dict[str, Any]) -> None:
    ctx.db.execute(
        "INSERT INTO incidents(id,kind,state,source,opened_at,timeline,photo_id,vision_note,acked_by,closed_at,"
        "calls_placed,data,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "state=excluded.state, timeline=excluded.timeline, photo_id=excluded.photo_id, vision_note=excluded.vision_note,"
        " acked_by=excluded.acked_by, closed_at=excluded.closed_at, calls_placed=excluded.calls_placed, "
        "data=excluded.data, updated_at=excluded.updated_at",
        (inc["id"], inc["kind"], inc["state"], inc["source"], inc["opened_at"], dumps(inc["timeline"]),
         inc.get("photo_id"), inc.get("vision_note"), inc.get("acked_by"), inc.get("closed_at"),
         inc.get("calls_placed", 0), dumps(inc), ctx.now.isoformat()))


def _to(ctx: Any, inc: dict[str, Any], new_state: str, why: str) -> None:
    inc["state"] = new_state
    inc["timeline"].append({"t": ctx.now.isoformat(), "state": new_state, "why": why})
    if new_state == "CLOSED":
        inc["closed_at"] = ctx.now.isoformat()
    _save(ctx, inc)


def _note(ctx: Any, inc: dict[str, Any], what: str, **kw: Any) -> None:
    inc["timeline"].append({"t": ctx.now.isoformat(), "what": what, **kw})
    _save(ctx, inc)


# ---------------------------------------------------------------- small command helpers
def _key(inc: dict[str, Any], name: str) -> str:
    return f"fall:{name}:{inc['id']}"


def _timer(inc: dict[str, Any], name: str, delay: float, **data: Any) -> Command:
    return cmd("timer.set", P0, inc["id"], key=_key(inc, name), delay_s=delay, data={"incident_id": inc["id"], **data})


def _cancel(inc: dict[str, Any], *names: str) -> list[Command]:
    return [cmd("timer.cancel", P0, inc["id"], key=_key(inc, n)) for n in names]


def _ui(inc: dict[str, Any], face: dict[str, Any], prio: int = P0) -> Command:
    return ui(face, prio, inc["id"])


def _play(ctx: Any, inc: dict[str, Any], clip: str, phase: str, prio: int = P0) -> list[Command]:
    n = len(inc["says"]) + len(inc.get("said", []))
    say_id = f"say_{inc['id']}_{clip}_{n}"
    inc["says"][say_id] = phase
    return [cmd("device.play_fixed", prio, inc["id"], clip_id=clip, say_id=say_id),
            cmd("timer.set", P0, inc["id"], key=f"fall:clip:{say_id}", delay_s=clip_max(ctx, clip),
                data={"incident_id": inc["id"], "say_id": say_id})]


def _step(inc: dict[str, Any], sid: str, st: str, label: str | None = None) -> Command | None:
    if sid not in inc["steps"]:
        return None
    inc["steps"][sid] = st
    f: dict[str, Any] = {"type": "step", "group": GROUP, "id": sid, "state": st}
    if label:
        f["label"] = label
    return _ui(inc, f)


def _group_cmd(ctx: Any, inc: dict[str, Any]) -> Command:
    cfg = _steps_cfg(ctx)
    labels = {"photo": (cfg.get("photo", {}).get("active", "分析緊相片…"), cfg.get("photo", {}).get("default_done", "相片已送出")),
              "ask": (cfg.get("ask", {}).get("active", "等婆婆回應"), cfg.get("ask", {}).get("done", "婆婆話冇事")),
              "call": (cfg.get("call", {}).get("active", "致電緊急中心"), cfg.get("call", {}).get("done", "已致電緊急中心")),
              "tell": (cfg.get("tell", {}).get("active", "通知阿珍"), cfg.get("tell", {}).get("done", "已通知阿珍"))}
    steps = [{"id": sid, "label": labels[sid][0], "doneLabel": labels[sid][1], "state": st}
             for sid, st in inc["steps"].items()]
    inc["group"] = True
    return _ui(inc, {"type": "steps", "group": {"id": GROUP, "title": "跌倒處理" if inc["kind"] != "concern" else "緊急處理",
                                                 "steps": steps}})


def _card(inc: dict[str, Any]) -> Command:
    return _ui(inc, {"type": "card", "card": {"id": CARD, "kind": "incident", "label": inc["card"]["label"],
                                              "small": inc["card"]["small"], "persistent": True}})


# ---------------------------------------------------------------- entry
CARD_TEXT = {
    "fall": ("偵測到跌倒", "感應器：急速下跌"),
    "sos": ("求救掣", "婆婆撳咗求救掣"),
    "concern": ("緊急關注", "婆婆話身體唔舒服"),
}


def _new(ctx: Any, kind: str, source: str, at: str) -> dict[str, Any]:
    label, small = CARD_TEXT[kind]
    if source == "operator":
        small = "操作員觸發"           # honest about its origin (INV-8)
    steps = {"photo": "pending", "ask": "pending", "call": "pending", "tell": "pending"}
    if at == "AWAITING_OK":
        steps.pop("photo")
    return {"id": new_id("inc"), "kind": kind, "state": "IDLE", "source": source, "opened_at": ctx.now.isoformat(),
            "timeline": [], "photo_id": None, "vision_note": None, "vision_short": None, "vision_requested": False,
            "photo_wait": False, "calls_placed": 0, "called": [], "acked_by": None, "closed_at": None,
            "says": {}, "said": [], "steps": steps, "group": False, "ok_shown": False,
            "card": {"label": label, "small": small}, "why": None}


def start(state: KernelState, ctx: Any, ev: Event, kind: str, at: str = "ALARM",
          detail: dict[str, Any] | None = None) -> list[Command]:
    """Open an incident (or attach to the open one). Shared with the concern reducer (`at="AWAITING_OK"`)."""
    cur = inc_of(state)
    if cur is not None and cur["state"] not in ("CLOSED", "IDLE"):
        _note(ctx, cur, "attached", event=ev.type, source=ev.source, **(detail or {}))
        return []
    if cur is not None and kind != "concern" and ev.type != "operator.fall" and ev.source != "operator":
        from datetime import datetime
        opened = datetime.fromisoformat(cur["opened_at"])
        if (ctx.now - opened).total_seconds() < COOLDOWN_S:      # one fall incident per 60 s
            _note(ctx, cur, "cooldown", event=ev.type, source=ev.source)
            return []
    source = "operator" if ev.source == "operator" else ev.source
    inc = _new(ctx, kind, source, at)
    state.incident = inc
    _to(ctx, inc, "ALARM" if at == "ALARM" else "AWAITING_OK", ev.type)
    if detail:
        _note(ctx, inc, "detail", **detail)
    out: list[Command] = []
    t = state.turn
    if t is not None and t.say_id and not t.done:       # Ah Bo's caption stops before the safety clip (INV-5)
        out.append(cmd("timer.cancel", P0, inc["id"], key=f"say_end:{t.say_id}"))
        out.append(_ui(inc, {"type": "say_end", "id": t.say_id}))
        out.append(cmd("agent.say_done", P0, inc["id"], say_id=t.say_id, interrupted=True))
    state.turn = None
    state.family_active = False
    out += arbiter.stop_playing(state)
    out.append(cmd("agent.pause", P0, inc["id"], hard=True, session_id=state.session.id if state.session else None))
    out += [_with_corr(c, inc) for c in set_mode(state, "kernel_listen", P0)]     # tools blocked from now on
    out.append(_ui(inc, {"type": "reset"}))
    out.append(_ui(inc, {"type": "mood", "mood": "alarm"}))
    out.append(_card(inc))
    if at == "ALARM":
        out += _play(ctx, inc, "F1", "F1")
        out.append(cmd("device.capture_photo", P0, inc["id"], incident_id=inc["id"]))
        out.append(cmd("device.wake_enable", P0, inc["id"], on=False))
        return out
    # emergency concern: straight to the OK question (no photo)
    out.append(_group_cmd(ctx, inc))
    return out + _ask(state, ctx, inc)


# ---------------------------------------------------------------- phases
def _photo_check(state: KernelState, ctx: Any, inc: dict[str, Any]) -> list[Command]:
    _to(ctx, inc, "PHOTO_CHECK", "f1_done")
    inc["steps"]["photo"] = "active"
    out = [_ui(inc, {"type": "flash"}), _group_cmd(ctx, inc)]
    out += _play(ctx, inc, "F2", "F2")
    out += _vision(inc)
    return out


def _vision(inc: dict[str, Any]) -> list[Command]:
    if inc.get("photo_id") and not inc["vision_requested"] and inc["state"] != "ALARM":
        inc["vision_requested"] = True
        return [cmd("vision.analyze", P1, inc["id"], incident_id=inc["id"], photo_id=inc["photo_id"])]
    return []


def _after_f2(state: KernelState, ctx: Any, inc: dict[str, Any]) -> list[Command]:
    if inc["vision_requested"] and not inc.get("vision_note") and not inc["photo_wait"]:
        inc["photo_wait"] = True            # the kit shows ✓ about 0.8 s after F2; a ready note is worth it
        return [_timer(inc, "photo_wait", PHOTO_WAIT_S)]
    return _awaiting(state, ctx, inc)


def _awaiting(state: KernelState, ctx: Any, inc: dict[str, Any]) -> list[Command]:
    out = _cancel(inc, "photo_wait")
    label = inc.get("vision_short") or _steps_cfg(ctx).get("photo", {}).get("default_done", "相片已送出")
    c = _step(inc, "photo", "done", label)
    if c:
        out.append(c)
    _to(ctx, inc, "AWAITING_OK", "f2_done")
    return out + _ask(state, ctx, inc)


def _ask(state: KernelState, ctx: Any, inc: dict[str, Any]) -> list[Command]:
    """AWAITING_OK: ask (F3); her words go to the OK-matcher through a kernel_listen session from now on."""
    out: list[Command] = []
    c = _step(inc, "ask", "active")
    if c:
        out.append(c)
    out += _play(ctx, inc, "F3", "F3")
    if state.session is None:
        out += open_session(state, ctx, reason="fall_check", mode="kernel_listen", priority=P0, listening_mood=False)
    else:
        out += set_mode(state, "kernel_listen", P0)
    return [_with_corr(c, inc) for c in out]


def _ok_prompt(state: KernelState, ctx: Any, inc: dict[str, Any]) -> list[Command]:
    """After F3 (BUILD_LOG D-8): the button, the countdown, the Kernel's own timer, a kernel_listen session."""
    pr = params(ctx)
    inc["ok_shown"] = True
    out = [_ui(inc, {"type": "ok_button", "show": True}),
           _ui(inc, {"type": "countdown", "id": "fall_cd", "n": pr["countdown"]}),
           _timer(inc, "countdown", float(pr["countdown"]))]
    if pr["repeat_f3"]:
        out.append(_timer(inc, "f3again", pr["repeat_f3"]))
    out.append(cmd("device.wake_enable", P0, inc["id"], on=False))
    if state.session is None:          # the session was closed meanwhile (e.g. by the operator): reopen it
        out += open_session(state, ctx, reason="fall_check", mode="kernel_listen", priority=P0, listening_mood=False)
    _note(ctx, inc, "ok_prompt", countdown=pr["countdown"])
    return [_with_corr(c, inc) for c in out]


def _with_corr(c: Command, inc: dict[str, Any]) -> Command:
    if c.correlation_id is None:
        c.correlation_id = inc["id"]
    return c


# ---------------------------------------------------------------- outcomes
def resolve(state: KernelState, ctx: Any, inc: dict[str, Any], how: str) -> list[Command]:
    if inc["state"] not in PRE:
        return []
    inc["acked_by"] = how
    _to(ctx, inc, "RESOLVED_OK", how)
    cfg = _steps_cfg(ctx)
    out = _cancel(inc, "countdown", "f3again", "photo_wait")
    out += [_ui(inc, {"type": "card_clear", "id": CARD}), _ui(inc, {"type": "card_clear"}),
            _ui(inc, {"type": "ok_button", "show": False})]
    if inc["steps"].get("photo") == "active":
        out.append(_step(inc, "photo", "done", cfg.get("photo", {}).get("default_done", "相片已送出")))
    for c in (_step(inc, "ask", "done"), _step(inc, "call", "failed", cfg.get("call", {}).get("skipped", "唔使致電"))):
        if c:
            out.append(c)
    out.append(_ui(inc, {"type": "mood", "mood": "warm"}))
    out += _play(ctx, inc, "F5", "F5")
    what = {"fall": "可能跌咗一跤", "sos": "撳咗求救掣", "concern": "話身體唔舒服"}[inc["kind"]]
    out.append(cmd("family.send", P1, inc["id"], kind="INFO", contact_ids=family_contact_ids(ctx),
                   text=f"王婆婆{what}，之後佢話冇事（{_how_words(how)}）。阿寶會繼續陪住佢。",
                   incident_id=inc["id"]))
    return out


def _how_words(how: str) -> str:
    return {"voice": "親口講", "touch": "撳咗「我冇事」掣", "operator": "操作員確認"}.get(how, how)


def escalate(state: KernelState, ctx: Any, inc: dict[str, Any], why: str) -> list[Command]:
    if inc["state"] not in PRE:
        return []
    inc["why"] = why
    _to(ctx, inc, "ESCALATING", why)
    cfg = _steps_cfg(ctx)
    pr = params(ctx)
    out = _cancel(inc, "countdown", "f3again", "photo_wait")
    if not inc["group"]:
        if "photo" in inc["steps"] and inc["steps"]["photo"] == "pending":
            inc["steps"]["photo"] = "active"
        out.append(_group_cmd(ctx, inc))
    out.append(_ui(inc, {"type": "ok_button", "show": False}))
    if inc["steps"].get("photo") == "active":
        out.append(_step(inc, "photo", "done", inc.get("vision_short") or cfg.get("photo", {}).get("default_done", "相片已送出")))
    ask_label = {"timeout": cfg.get("ask", {}).get("failed", "冇回應"), "help": "婆婆話要幫手",
                 "operator": "操作員升級"}.get(why, cfg.get("ask", {}).get("failed", "冇回應"))
    out += [c for c in (_step(inc, "ask", "failed", ask_label), _step(inc, "call", "active"),
                        _step(inc, "tell", "active")) if c]
    demo = pr["f4"] == "F4"
    out.append(_ui(inc, {"type": "card", "card": {"id": "esc_call", "kind": "action", "label": "緊急中心",
                                                  "small": "已致電", "when": "救護車" if demo else "緊急中心"}}))
    out.append(_ui(inc, {"type": "card", "card": {"id": "esc_tell", "kind": "message", "label": "已通知阿珍",
                                                  "small": "訊息已送出", "when": "阿珍"}}))
    calls = _call_next(ctx, inc)
    out += calls
    out.append(_alert(ctx, inc))
    out += [c for c in (_step(inc, "tell", "done"),
                        _step(inc, "call", "done") if calls else _step(inc, "call", "failed", "冇可致電聯絡人")) if c]
    out += _play(ctx, inc, pr["f4"], "F4")
    out.append(_timer(inc, "tack", pr["t_ack"]))
    return out


def _alert(ctx: Any, inc: dict[str, Any]) -> Command:
    what = {"fall": "懷疑跌倒", "sos": "撳咗求救掣", "concern": "話身體唔舒服（緊急）"}[inc["kind"]]
    why = {"timeout": "冇回應", "help": "佢話要幫手", "operator": "操作員升級"}.get(inc.get("why") or "", "需要跟進")
    src = "（操作員觸發）" if inc["source"] == "operator" else ""
    text = f"王婆婆喺屋企{what}{src}，{why}。已致電緊急中心（模擬）。地址：{address(ctx)}"
    payload: dict[str, Any] = {"kind": "ALERT", "contact_ids": family_contact_ids(ctx), "text": text,
                               "buttons": ["handled", "call_mum", "resolved"], "incident_id": inc["id"],
                               "time": ctx.now.strftime("%H:%M"), "address": address(ctx)}
    if inc.get("photo_id"):
        payload["photo_id"] = inc["photo_id"]
    if inc.get("vision_note"):
        payload["vision_note"] = inc["vision_note"]        # advisory, shown as such by the Family Link
    return cmd("family.send", P0, inc["id"], **payload)


def _call_next(ctx: Any, inc: dict[str, Any]) -> list[Command]:
    """INV-9: the next verified, allowed contact (centre first, then family by priority, then around again)."""
    if inc["calls_placed"] >= MAX_CALLS:
        return []
    order = [c for c in call_order(ctx) if callable_contact(c)]
    if not order:
        _note(ctx, inc, "no_callable_contact")
        return []
    fresh = [c for c in order if c["id"] not in inc["called"]]
    contact = fresh[0] if fresh else order[inc["calls_placed"] % len(order)]
    inc["calls_placed"] += 1
    inc["called"].append(contact["id"])
    call_id = new_id("call")
    _note(ctx, inc, "call", contact_id=contact["id"], call_id=call_id)
    return [cmd("phone.call", P0, inc["id"], contact_id=contact["id"], clip="P1", incident_id=inc["id"],
                call_id=call_id)]


def _reassure(state: KernelState, ctx: Any, inc: dict[str, Any]) -> list[Command]:
    if inc["state"] != "ESCALATING":
        return []
    _to(ctx, inc, "REASSURING", "f4_done")
    out = [_ui(inc, {"type": "mood", "mood": "warm"}, P1),
           cmd("agent.resume", P1, inc["id"], mode="reassure")]
    if state.session is None:
        out += open_session(state, ctx, reason="reassure", mode="reassure", priority=P1, listening_mood=False)
    else:
        out += set_mode(state, "reassure", P1)
    return [_with_corr(c, inc) for c in out]


def acknowledge(state: KernelState, ctx: Any, inc: dict[str, Any], by: str) -> list[Command]:
    if inc["state"] not in ("ESCALATING", "REASSURING"):
        return []
    inc["acked_by"] = by
    _to(ctx, inc, "ACKNOWLEDGED", by)
    name = "阿珍"
    row = ctx.db.one("SELECT name FROM contacts WHERE id=?", (by,))
    if row:
        name = row["name"]
    return _cancel(inc, "tack") + [
        _ui(inc, {"type": "mood", "mood": "warm"}, P1),
        cmd("family.send", P1, inc["id"], kind="NOTIFY", contact_ids=family_contact_ids(ctx),
            text=f"收到，{name}跟進緊王婆婆嘅事。阿寶會繼續陪住佢。", incident_id=inc["id"])]


def close(state: KernelState, ctx: Any, inc: dict[str, Any], why: str, steps_cleared: bool = False) -> list[Command]:
    if inc["state"] in ("CLOSED", "IDLE"):
        return []
    _to(ctx, inc, "CLOSED", why)
    out = _cancel(inc, "countdown", "f3again", "photo_wait", "tack", "clear")
    face = ctx.ui                       # clear only what is still showing
    if face.ok_button:
        out.append(_ui(inc, {"type": "ok_button", "show": False}, P1))
    for cid in (CARD, "esc_call", "esc_tell"):
        if cid in face.cards:
            out.append(_ui(inc, {"type": "card_clear", "id": cid}, P1))
    if GROUP in face.groups and not steps_cleared:
        out.append(_ui(inc, {"type": "steps_clear", "group": GROUP}, P1))
    if face.mood in ("alarm", "warm"):
        out.append(_ui(inc, {"type": "mood", "mood": "idle"}, P1))
    out.append(cmd("agent.resume", P1, inc["id"], mode="companion"))
    closing = [_with_corr(c, inc) for c in close_session(state, ctx, why=f"incident_{why}")]
    out += closing or [cmd("device.wake_enable", P1, inc["id"], on=True)]
    return out


def status_words(inc: dict[str, Any] | None) -> tuple[str, str]:
    if inc is None:
        return "IDLE", "而家冇事發生。"
    st = inc["state"]
    if st in PRE:
        return st, "阿寶等緊你回應，如果冇事就講「我冇事」。"
    if st in ("ESCALATING", "REASSURING"):
        called = "已經打咗電話俾緊急中心，" if inc["calls_placed"] else ""
        return st, f"{called}已經通知咗阿珍，佢哋會盡快跟進。"
    if st == "ACKNOWLEDGED":
        return st, "阿珍收到消息，跟進緊喇。"
    return st, "冇事喇，阿寶喺度陪住你。"


# ---------------------------------------------------------------- the reducer
def _clip_done(state: KernelState, ctx: Any, inc: dict[str, Any], say_id: str) -> list[Command]:
    phase = inc["says"].pop(say_id, None)
    if phase is None:
        return []
    inc["said"].append(say_id)
    out = [cmd("timer.cancel", P0, inc["id"], key=f"fall:clip:{say_id}")]
    st = inc["state"]
    if phase == "F1" and st == "ALARM":
        out += _photo_check(state, ctx, inc)
    elif phase == "F2" and st == "PHOTO_CHECK":
        out += _after_f2(state, ctx, inc)
    elif phase == "F3" and st == "AWAITING_OK" and not inc["ok_shown"]:
        out += _ok_prompt(state, ctx, inc)
    elif phase == "F4":
        out += _reassure(state, ctx, inc)
    elif phase == "F5" and st == "RESOLVED_OK":
        out += [_ui(inc, {"type": "mood", "mood": "idle"}, P1), _timer(inc, "clear", CLEAR_AFTER_F5_S)]
    _save(ctx, inc)
    return out


def _button(p: dict[str, Any]) -> str | None:
    raw = str(p.get("button") or p.get("callback_data") or "")
    if raw.startswith("b:"):
        parts = raw.split(":", 2)
        raw = parts[1] if len(parts) > 1 else ""
    return raw or None


def reduce(state: KernelState, ev: Event, ctx: Any) -> list[Command] | None:
    t, p, src = ev.type, ev.payload, ev.source
    # ---- entry
    if t == "sensor.fall_suspected" and src in ("beacon", "operator"):
        return start(state, ctx, ev, "fall", detail={k: p[k] for k in ("peak_g", "beacon_id") if k in p})
    if t == "sensor.sos_pressed" and src in ("beacon", "operator"):
        return start(state, ctx, ev, "sos")
    if t == "operator.fall" and src == "operator":
        return start(state, ctx, ev, "fall")
    if t == "incident.status_request":
        st, words = status_words(inc_of(state))
        ctx.reply(ev, {"ok": True, "state": st, "words": words})
        return None
    inc = inc_of(state)
    if inc is None or inc["state"] in ("CLOSED", "IDLE"):
        return None
    st = inc["state"]
    mine = ev.correlation_id in (None, inc["id"]) or p.get("incident_id") == inc["id"]

    if t == "device.playback_done" and p.get("say_id") in inc["says"]:
        return _clip_done(state, ctx, inc, p["say_id"])
    if t == "timer.fired":
        key, data = p.get("key", ""), p.get("data") or {}
        if data.get("incident_id") != inc["id"]:
            return None
        if key.startswith("fall:clip:"):
            return _clip_done(state, ctx, inc, data.get("say_id", ""))
        name = key.split(":")[1] if key.count(":") >= 2 else ""
        if name == "photo_wait" and st == "PHOTO_CHECK":
            return _awaiting(state, ctx, inc)
        if name == "countdown" and st == "AWAITING_OK":
            return escalate(state, ctx, inc, "timeout")
        if name == "f3again" and st == "AWAITING_OK":
            return _play(ctx, inc, "F3", "F3r")
        if name == "clear" and st == "RESOLVED_OK":
            return [_ui(inc, {"type": "steps_clear", "group": GROUP}, P1)] + \
                close(state, ctx, inc, "resolved_ok", steps_cleared=True)
        if name == "tack" and st in ("ESCALATING", "REASSURING"):
            out = _call_next(ctx, inc)
            if inc["calls_placed"] < MAX_CALLS:
                out.append(_timer(inc, "tack", params(ctx)["t_ack"]))
            _save(ctx, inc)
            return out
        if name == "resume" and st in ("ALARM", "PHOTO_CHECK"):      # restart recovery
            return _awaiting(state, ctx, inc)
        if name == "resume" and st == "AWAITING_OK":
            inc["ok_shown"] = False
            return _ok_prompt(state, ctx, inc)
        return None

    if t == "device.photo_uploaded" and p.get("incident_id") == inc["id"]:
        inc["photo_id"] = p.get("photo_id")
        _save(ctx, inc)
        return _vision(inc)
    if t == "vision.result" and (p.get("incident_id") == inc["id"] or ev.correlation_id == inc["id"]):
        # advisory only (INV-2/INV-7): stored, maybe shown as a concern-raising label, never a state change
        inc["vision_note"] = str(p.get("note") or "")[:300] or None
        inc["vision_short"] = vision_short(inc["vision_note"])
        _save(ctx, inc)
        if st == "PHOTO_CHECK" and inc["photo_wait"]:
            return _awaiting(state, ctx, inc)
        return None

    # ---- de-escalation: deterministic signals only (INV-2)
    if st in PRE:
        if t == "user.utterance" and p.get("final", True):
            m = match_ok(str(p.get("text") or ""), ctx.config)
            if m == "help":
                return escalate(state, ctx, inc, "help")
            if m == "ok":
                return resolve(state, ctx, inc, "voice")
            return None
        if t == "device.touch_ok" and src == "device":
            return resolve(state, ctx, inc, "touch")
        if t == "operator.ok" and src == "operator":
            return resolve(state, ctx, inc, "operator")
        if t == "operator.escalate" and src == "operator":
            return escalate(state, ctx, inc, "operator")
    if st in ("ESCALATING", "REASSURING"):
        if t == "operator.ok" and src == "operator":
            return acknowledge(state, ctx, inc, "operator")
        if t == "phone.call_status" and src in ("twilio", "operator") and mine:
            status = p.get("status")
            _note(ctx, inc, "call_status", status=status, call_id=p.get("call_id"))
            if status == "acknowledged":
                row = ctx.db.one("SELECT to_contact FROM outbox WHERE id=?", (p.get("outbox_id"),)) \
                    if p.get("outbox_id") else None
                return acknowledge(state, ctx, inc, (row or {}).get("to_contact") or "phone")
            if status in ("no-answer", "busy", "failed"):
                return _call_next(ctx, inc)
            return None
    if t == "family.button" and src in ("telegram", "operator") and mine:
        b = _button(p)
        _note(ctx, inc, "family_button", button=b, contact_id=p.get("contact_id"))
        if b == "handled":
            return acknowledge(state, ctx, inc, p.get("contact_id") or "family")
        if b == "resolved":
            return close(state, ctx, inc, "family_resolved")
        return None
    if t == "operator.resolve" and src == "operator":
        return close(state, ctx, inc, "operator_resolve")
    return None


# ---------------------------------------------------------------- restart recovery + dev fallback
def _loaded(kernel: Any) -> None:
    if kernel.state.incident is not None:
        return
    row = kernel.db.one("SELECT data FROM incidents WHERE state NOT IN ('CLOSED','IDLE') ORDER BY opened_at DESC LIMIT 1")
    if not row:
        return
    inc = loads(row["data"], None)
    if not isinstance(inc, dict) or "id" not in inc:
        return
    inc["says"] = {}
    kernel.state.incident = inc
    st = inc["state"]
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    if st in PRE:          # the Kernel's timers died with the process: ask again, fresh countdown (never silently drop)
        kernel._set_timer(f"fall:resume:{inc['id']}", 0.1, {"incident_id": inc["id"]}, inc["id"])
    elif st in ("ESCALATING", "REASSURING"):
        kernel._set_timer(f"fall:tack:{inc['id']}", params(kernel.ctx())["t_ack"], {"incident_id": inc["id"]},
                          inc["id"])
    elif st == "RESOLVED_OK":
        kernel._set_timer(f"fall:clear:{inc['id']}", 0.1, {"incident_id": inc["id"]}, inc["id"])


def _fallback_vision(kernel: Any):
    """Dev/test stand-in for the models component's Vision adapter (fake mode only; any real registration replaces
    it): answers like the PRD's fake (§26.3) 1.5 s later. Registered on the target key `vision`, so a component that
    registers `vision.analyze` or `vision` wins."""
    def dispatch(c: Command) -> None:
        async def run() -> None:
            await asyncio.sleep(1.5 * kernel.time_scale)
            iid = c.payload.get("incident_id")
            await kernel.submit(Event(type="vision.result", source="system", correlation_id=iid,
                                      payload={"incident_id": iid, "note": FALLBACK_VISION_NOTE, "fake": True}))
        kernel.spawn(run(), name="fallback_vision")
    return dispatch


def setup(kernel: Any) -> None:
    if _loaded not in kernel.loaded_hooks:
        kernel.loaded_hooks.append(_loaded)
    if kernel.settings.is_fake("vision") and "vision" not in kernel.dispatchers \
            and "vision.analyze" not in kernel.dispatchers:
        kernel.on("vision", _fallback_vision(kernel))
