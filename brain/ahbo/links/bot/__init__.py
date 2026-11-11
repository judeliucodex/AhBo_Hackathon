"""Telegram caregiver + admin bot (owner decision 11 Nov 2026: Telegram replaces the web operator console).

Only the allowlisted family chat reaches this package (`ahbo.links.telegram` drops strangers first, INV-7).
Entry points (each returns a list of `Reply` for the channel to send, or None):
- `command(svc, contact, chat_id, text)`  — a typed slash command (menus, views, demo controls, shortcuts);
- `text(svc, contact, chat_id, text)`     — plain text: an answer to the open form / pending confirm, else None
                                           (then the text is relayed to Po-po as before);
- `callback(svc, contact, chat_id, data)` — an inline button with `m:` callback data.

Every change goes through a 「確認／取消」 step (`pending` in the per-chat conversation state, `state.py`) and
then `changes.apply`, which records the Kernel event.
"""
from __future__ import annotations

import logging
from typing import Any

from . import changes, forms, state, views
from .changes import BotError
from .ui import HELP, Reply, back_row, cb, confirm_buttons, main_menu

log = logging.getLogger("ahbo.links.bot")

VIEW_CMDS = {"/today": "today", "/status": "today", "/meds": "meds", "/cal": "cal", "/calendar": "cal",
             "/settings": "set", "/contacts": "contacts", "/alerts": "alerts", "/health": "health", "/demo": "demo",
             "/knobs": "knobs"}
ADMIN_CMDS = {"/reset": "reset", "/panic": "panic", "/fall": "fall", "/sos": "sos",
              "/mute": "mute", "/unmute": "unmute"}
SCENARIOS = ("S1", "S2", "S3", "S4", "S4ok")
CANCEL_WORDS = ("取消", "/cancel", "cancel")
CONFIRM_WORDS = ("確認", "确认", "ok", "OK", "好")


# ================================================================ views
async def view(svc: Any, name: str, arg: str | None = None) -> Reply:
    simple = {"today": views.today_view, "meds": views.meds_view, "cal": views.cal_view, "set": views.settings_view,
              "contacts": views.contacts_view, "alerts": views.alerts_view, "demo": views.demo_view,
              "knobs": views.knobs_view, "dose_pick": views.dose_pick_view, "ck_pick": views.checkin_pick_view}
    if name in simple:
        return simple[name](svc)
    if name == "health":
        return await views.health_view(svc)
    if arg and name in ("slot", "med", "ev", "contact"):
        return {"slot": views.slot_view, "med": views.med_view, "ev": views.event_view,
                "contact": views.contact_view}[name](svc, arg)
    return main_menu()


# ================================================================ confirm step
def propose(svc: Any, contact: dict[str, Any], chat_id: str, st: dict[str, Any], op: str,
            args: dict[str, Any]) -> list[Reply]:
    try:
        summary = changes.describe(svc, contact, op, args)
    except BotError as e:
        st["form"] = None
        state.save(svc.db, chat_id, st)
        return [Reply(f"⚠️ {e}", [back_row()])]
    nonce = state.new_nonce()
    st["form"] = None
    st["pending"] = {"op": op, "args": args, "summary": summary, "nonce": nonce}
    state.save(svc.db, chat_id, st)
    return [Reply(f"{summary}\n\n確認？", confirm_buttons(nonce))]


async def confirm(svc: Any, contact: dict[str, Any], chat_id: str, nonce: str | None, ok: bool) -> list[Reply]:
    st = state.load(svc.db, chat_id)
    pend = st.get("pending")
    if not pend or (nonce is not None and pend.get("nonce") != nonce):
        return [Reply("呢個確認已經過期或者做咗喇，冇改到嘢。", [back_row()])]
    st["pending"] = None
    state.save(svc.db, chat_id, st)
    if not ok:
        return [Reply("已取消，冇改到嘢。", [back_row()])]
    try:
        done = await changes.apply(svc, contact, pend["op"], pend["args"])
    except BotError as e:
        return [Reply(f"⚠️ 做唔到：{e}", [back_row()])]
    except Exception:
        log.exception("bot change %s failed", pend.get("op"))
        return [Reply("⚠️ 出咗錯，冇改到嘢。", [back_row()])]
    return [Reply(f"✅ {done}", [back_row()])]


# ================================================================ forms
def start_form(svc: Any, contact: dict[str, Any], chat_id: str, name: str, item_id: str | None) -> list[Reply]:
    form, err = forms.start(svc, name, item_id)
    if form is None:
        return [err or Reply("唔得。")]
    st = state.load(svc.db, chat_id)
    st["form"], st["pending"] = form, None
    state.save(svc.db, chat_id, st)
    return [forms.step_reply(svc, form)]


def form_input(svc: Any, contact: dict[str, Any], chat_id: str, st: dict[str, Any], raw: str,
               *, button: bool) -> list[Reply]:
    form = st["form"]
    kind, out = forms.answer(svc, form, raw, button=button)
    if kind == "done":
        op, args = out
        return propose(svc, contact, chat_id, st, op, args)
    st["form"] = form
    state.save(svc.db, chat_id, st)
    return [out]


# ================================================================ entry points
async def command(svc: Any, contact: dict[str, Any], chat_id: str, text: str) -> list[Reply]:
    head, _, rest = text.strip().partition(" ")
    cmd_ = head.split("@", 1)[0].lower()
    rest = rest.strip()
    if cmd_ in ("/start", "/menu"):
        state.clear(svc.db, chat_id)
        return [main_menu(contact.get("name"))]
    if cmd_ == "/help":
        return [Reply(HELP, [back_row()])]
    if cmd_ == "/cancel":
        state.clear(svc.db, chat_id)
        return [Reply("已取消。", [back_row()])]
    if cmd_ in VIEW_CMDS:
        return [await view(svc, VIEW_CMDS[cmd_])]
    st = state.load(svc.db, chat_id)
    if cmd_ in ADMIN_CMDS:
        return propose(svc, contact, chat_id, st, ADMIN_CMDS[cmd_], {})
    if cmd_ == "/scenario":
        name = next((s for s in SCENARIOS if s.lower() == rest.lower()), None)
        if name is None:
            return [Reply("用法：/scenario S1|S2|S3|S4|S4ok",
                          [[(s, cb("a", "scen", s)) for s in SCENARIOS], back_row()])]
        return propose(svc, contact, chat_id, st, "scen", {"name": name})
    if cmd_ == "/clock":
        if not rest:
            return start_form(svc, contact, chat_id, "clock", None)
        try:
            c = forms.parse_clock(rest, svc)
        except BotError as e:
            return [Reply(f"⚠️ {e}\n用法：/clock 07:58 | /clock +30 | /clock now | /clock S2")]
        return propose(svc, contact, chat_id, st, c["op"], c["args"])
    if cmd_ == "/dose":
        if not rest:
            return [views.dose_pick_view(svc)]
        slot = svc.db.one("SELECT slot_id FROM med_schedule WHERE slot_id=? OR time=?", (rest, rest))
        if slot is None:
            try:
                slot = svc.db.one("SELECT slot_id FROM med_schedule WHERE time=?", (forms.parse_time(rest),))
            except BotError:
                slot = None
        if slot is None:
            return [views.dose_pick_view(svc)]
        return propose(svc, contact, chat_id, st, "dose", {"slot_id": slot["slot_id"]})
    if cmd_ == "/checkin":
        if rest not in ("morning", "afternoon", "evening"):
            return [views.checkin_pick_view(svc)]
        return propose(svc, contact, chat_id, st, "ck", {"kind": rest})
    if cmd_ == "/say":
        if not rest:
            return start_form(svc, contact, chat_id, "say", None)
        try:
            line = forms.parse_text(rest, forms.FORMS["say"].fields[0])
        except BotError as e:
            return [Reply(f"⚠️ {e}")]
        return propose(svc, contact, chat_id, st, "say", {"text": line})
    if cmd_ == "/thinking":
        parts = rest.lower().split()
        flash_lv, sonnet_lv = ("off", "low", "high", "max"), ("off", "low", "medium", "high", "xhigh", "max")
        if len(parts) == 2 and (parts[0], parts[1]) in [("flash", lv) for lv in flash_lv] + \
                [("sonnet", lv) for lv in sonnet_lv]:
            return propose(svc, contact, chat_id, st, parts[0], {"level": parts[1]})
        return [views.knobs_view(svc)]
    if cmd_ == "/quiet":
        parts = rest.split()
        if len(parts) != 2:
            return start_form(svc, contact, chat_id, "quiet", None)
        try:
            patch = {"quiet_start": forms.parse_time(parts[0]), "quiet_end": forms.parse_time(parts[1])}
        except BotError as e:
            return [Reply(f"⚠️ {e}\n用法：/quiet 22:30 07:00")]
        return propose(svc, contact, chat_id, st, "settings", {"patch": patch})
    if cmd_ == "/checkintime":
        parts = rest.split()
        if len(parts) != 2 or parts[0] not in ("morning", "afternoon", "evening"):
            return [Reply("用法：/checkintime morning|afternoon|evening 08:30", [back_row()])]
        try:
            hhmm = forms.parse_time(parts[1])
        except BotError as e:
            return [Reply(f"⚠️ {e}")]
        return propose(svc, contact, chat_id, st, "settings", {"patch": {parts[0]: hhmm}})
    return [Reply("唔知呢個指令。打 /help 睇晒全部指令，或者 /menu。", [back_row()])]


async def text(svc: Any, contact: dict[str, Any], chat_id: str, raw: str) -> list[Reply] | None:
    """An answer to the open form or pending confirm; None means "not for the bot" (relay to Po-po)."""
    st = state.load(svc.db, chat_id)
    s = raw.strip()
    if st.get("pending"):
        if s in CONFIRM_WORDS:
            return await confirm(svc, contact, chat_id, None, True)
        if s in CANCEL_WORDS:
            return await confirm(svc, contact, chat_id, None, False)
        return [Reply("請撳「確認」或者「取消」（或者打 確認／取消）。",
                      confirm_buttons(st["pending"]["nonce"]))]
    if st.get("form"):
        if s in CANCEL_WORDS:
            state.clear(svc.db, chat_id)
            return [Reply("已取消，冇改到嘢。", [back_row()])]
        return form_input(svc, contact, chat_id, st, s, button=False)
    return None


async def callback(svc: Any, contact: dict[str, Any], chat_id: str, data: str) -> list[Reply]:
    parts = data.split(":")
    if not parts or parts[0] != "m" or len(parts) < 2:
        return [Reply("呢個掣已經唔用喇。", [back_row()])]
    verb, args = parts[1], parts[2:]
    if verb == "menu":
        return [main_menu(contact.get("name"))]
    if verb == "v" and args:
        return [await view(svc, args[0], args[1] if len(args) > 1 else None)]
    if verb == "f" and args:
        return start_form(svc, contact, chat_id, args[0], args[1] if len(args) > 1 else None)
    if verb in ("ok", "no"):
        return await confirm(svc, contact, chat_id, args[0] if args else "", verb == "ok")
    if verb == "x":
        state.clear(svc.db, chat_id)
        return [Reply("已取消，冇改到嘢。", [back_row()])]
    st = state.load(svc.db, chat_id)
    if verb == "i":
        if not st.get("form"):
            return [Reply("呢個表格已經過期（十分鐘冇郁）。請由頭再嚟。", [back_row()])]
        return form_input(svc, contact, chat_id, st, ":".join(args), button=True)
    if verb == "a" and args:
        op, arg = args[0], (args[1] if len(args) > 1 else None)
        one_shot = {"slot_toggle": ("slot_toggle", {"slot_id": arg}), "slot_del": ("slot_delete", {"slot_id": arg}),
                    "med_del": ("med_delete", {"id": arg}), "cal_cancel": ("cal_cancel", {"id": arg}),
                    "contact_del": ("contact_delete", {"id": arg}), "verify": ("verify", {"id": arg}),
                    "scen": ("scen", {"name": arg}), "dose": ("dose", {"slot_id": arg}), "ck": ("ck", {"kind": arg}),
                    "flash": ("flash", {"level": arg}), "sonnet": ("sonnet", {"level": arg})}
        if op in one_shot:
            if arg is None:
                return [Reply("呢個掣唔齊資料。", [back_row()])]
            if op == "scen" and arg not in SCENARIOS:
                return [Reply("唔知呢個場景。", [back_row()])]
            return propose(svc, contact, chat_id, st, *one_shot[op])
        if op in changes.ADMIN_OPS and op not in ("dose", "ck", "scen", "clock", "say", "flash", "sonnet"):
            return propose(svc, contact, chat_id, st, op, {})
    return [Reply("呢個掣已經唔用喇。", [back_row()])]
