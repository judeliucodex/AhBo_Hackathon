"""Turn reducer: the face around one conversational turn (§23.3, §23.6, §23.7), in the kit's order:

  her words  → mood listening, heard            (text sessions also echo `heard`)
  processing → clear the previous turn's steps and cards, mood thinking
  tools      → step active (planned steps pending) … step done/failed with the filled label (ui_steps.yaml)
  reply      → reply mood (warm/curious/idle) → the turn's cards in the order the reply mentions them → caption
  reply end  → mood back to idle; the step group clears 4 s later

Events: user.utterance, agent.steps_plan, agent.tool_call, agent.tool_result, agent.reply_start,
agent.reply_end, timer.fired(say_end:*, steps_clear:*).
"""
from __future__ import annotations

import re
from typing import Any

from ahbo.core.events import P2, Command, Event, cmd, ui
from ahbo.core.ids import new_id

from ..state import KernelState, TurnState
from ..ui_state import public_card, turn_mood

FAKE_CHARS_PER_SEC = 5.2   # the kit's dev.html pace; fake speech durations use the same


def say_duration(text: str) -> float:
    return max(1.2, len(text) / FAKE_CHARS_PER_SEC)


def fill(template: str, vars_: dict[str, Any]) -> str:
    def rep(m: re.Match) -> str:
        v = vars_.get(m.group(1))
        return "" if v is None else str(v)
    return re.sub(r"\{(\w+)\}", rep, template)


def step_spec(cfg: Any, tool: str) -> dict[str, Any] | None:
    return (cfg.ui_steps.get("tools") or {}).get(tool)


def _quiet(ctx: Any) -> bool:
    from ..common import quiet_hours
    return quiet_hours(ctx)


def _mood(state: KernelState, ctx: Any, *, thinking: bool = False, listening: bool = False) -> Command:
    t = state.turn
    m = turn_mood(state, tone=t.tone if (t and t.replying) else None, thinking=thinking, listening=listening,
                  quiet=_quiet(ctx))
    return ui({"type": "mood", "mood": m}, P2)


def _active_label(spec: dict[str, Any], vars_: dict[str, Any]) -> str:
    return fill(spec.get("active", ""), vars_)


def _order_cards(cards: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    """Cards appear in the order the reply mentions them; each gets the first `when` phrase found in the text."""
    ranked = []
    for i, c in enumerate(cards):
        c = dict(c)
        cands = [w for w in (c.get("when_candidates") or []) + ([c["when"]] if c.get("when") else []) if w]
        pos, chosen = 10**6, None
        for w in cands:
            at = text.find(w) if text else -1
            if at > -1 and at < pos:
                pos, chosen = at, w
        if chosen:
            c["when"] = chosen
        elif c.get("when") and (not text or c["when"] not in text):
            c.pop("when", None)   # never leave a card waiting for a phrase that will not be spoken
        ranked.append((pos, i, c))
    ranked.sort(key=lambda r: (r[0], r[1]))
    return [r[2] for r in ranked]


def reduce(state: KernelState, ev: Event, ctx: Any) -> list[Command] | None:
    t, p = ev.type, ev.payload
    turn = state.turn

    if t == "user.utterance" and p.get("final", True):
        sess = state.session
        if sess is None or sess.mode in ("kernel_listen", "paused") or \
                (state.incident_active and sess.mode != "reassure"):
            return None
        out: list[Command] = []
        # Barge-in: the previous reply is cut (§24.6)
        if turn and turn.replying and turn.say_id and not turn.done:
            out.append(cmd("timer.cancel", key=f"say_end:{turn.say_id}"))
            out.append(ui({"type": "say_end", "id": turn.say_id}))
            out.append(cmd("agent.say_done", say_id=turn.say_id, interrupted=True))
        if turn:
            turn.replying, turn.done = False, True
        out.append(_mood(state, ctx, listening=True))
        if sess.text_only:
            out.append(ui({"type": "heard", "text": p.get("text", ""), "final": True}))
        # Processing starts: clear the previous turn from the face
        for gid in list(ctx.ui.groups):
            if gid != "fall":
                out.append(cmd("timer.cancel", key=f"steps_clear:{gid}"))
                out.append(ui({"type": "steps_clear", "group": gid}))
        if ctx.ui.has_cards():
            out.append(ui({"type": "card_clear"}))
        state.turn = TurnState(turn_id=p.get("turn_id") or new_id("turn"), session_id=sess.id,
                               text=p.get("text", ""))
        out.append(_mood(state, ctx, thinking=True))
        return out

    if _adopt(state, t, p):          # a turn the agent started itself (an arbiter inject): show it like any turn
        turn = state.turn
    if turn is None or p.get("turn_id") not in (None, turn.turn_id):
        if t == "timer.fired":
            return _timer(state, p, ctx)
        return None

    if t == "agent.steps_plan":
        specs = []
        for item in p.get("tools", []):
            name = item if isinstance(item, str) else item.get("name")
            args = {} if isinstance(item, str) else {**(item.get("args") or {}), **(item.get("vars") or {})}
            spec = step_spec(ctx.config, name)
            if spec:
                specs.append((name, args, spec))
        specs = specs[: int(ctx.config.ui_steps.get("max_steps_per_group", 4))]
        if not specs or turn.group_open:
            return None
        steps = []
        for i, (name, args, spec) in enumerate(specs):
            sid = name if name not in turn.steps else f"{name}_{i}"
            turn.steps[sid] = {"tool": name, "state": "active" if i == 0 else "pending"}
            turn.planned.append(sid)
            steps.append({"id": sid, "label": _active_label(spec, _vars_from_args(name, args, ctx)),
                          "state": "active" if i == 0 else "pending"})
        turn.group_open = True
        return [ui({"type": "steps", "group": {"id": turn.turn_id, "steps": steps}})]

    if t == "agent.tool_call":
        name, call_id = p.get("name"), p.get("call_id")
        spec = step_spec(ctx.config, name)
        if not spec:
            return None
        sid = next((s for s in turn.planned if turn.steps[s]["tool"] == name and s not in turn.call_steps.values()
                    and turn.steps[s]["state"] in ("pending", "active")), None)
        out = []
        label = _active_label(spec, {**_vars_from_args(name, p.get("args") or {}, ctx), **(p.get("vars") or {})})
        if sid is None:
            if len(turn.steps) >= int(ctx.config.ui_steps.get("max_steps_per_group", 4)):
                return None
            sid = name if name not in turn.steps else f"{name}_{len(turn.steps)}"
            turn.steps[sid] = {"tool": name, "state": "active"}
            if not turn.group_open:
                turn.group_open = True
                out.append(ui({"type": "steps", "group": {"id": turn.turn_id, "steps": [
                    {"id": sid, "label": label, "state": "active"}]}}))
            else:
                out.append(ui({"type": "step", "group": turn.turn_id, "id": sid, "state": "active", "label": label}))
        elif turn.steps[sid]["state"] == "pending":
            turn.steps[sid]["state"] = "active"
            out.append(ui({"type": "step", "group": turn.turn_id, "id": sid, "state": "active"}))
        turn.call_steps[call_id] = sid
        return out

    if t == "agent.tool_result":
        out = []
        for c in p.get("cards") or []:
            turn.pending_cards.append(c)
        if p.get("tone") and not turn.tone_hint:
            turn.tone_hint = p["tone"]
        sid = turn.call_steps.get(p.get("call_id"))
        spec = step_spec(ctx.config, p.get("name"))
        if sid and spec:
            step = p.get("step") or {}
            outcome = step.get("outcome")
            if outcome == "pending":          # e.g. deep_task: stays active until the result arrives
                return out
            vars_ = step.get("vars") or {}
            if p.get("ok") and outcome != "failed":
                done = spec.get("done") or {}
                tmpl = done.get(outcome) if isinstance(done, dict) else done
                if tmpl is None and isinstance(done, dict):
                    tmpl = next(iter(done.values()), "")
                turn.steps[sid]["state"] = "done"
                out.append(ui({"type": "step", "group": turn.turn_id, "id": sid, "state": "done",
                               "label": fill(tmpl or "", vars_)}))
            else:
                turn.steps[sid]["state"] = "failed"
                lab = spec.get("failed")
                f = {"type": "step", "group": turn.turn_id, "id": sid, "state": "failed"}
                if lab:
                    f["label"] = fill(lab, vars_)
                out.append(ui(f))
        return out

    if t == "agent.reply_start":
        text = p.get("text", "")
        turn.tone = p.get("tone") or turn.tone_hint
        turn.replying = True
        out = [_mood(state, ctx)]
        for c in _order_cards(turn.pending_cards, text):
            out.append(ui({"type": "card", "card": public_card(c)}))
        turn.pending_cards = []
        sess = state.session
        if sess and sess.text_only and text:
            turn.say_id = p.get("say_id") or f"say_{turn.turn_id}_{len(text)}"
            dur = say_duration(text)
            out.append(ui({"type": "say_start", "id": turn.say_id, "text": text, "durationSec": round(dur, 2)}))
            out.append(cmd("timer.set", key=f"say_end:{turn.say_id}", delay_s=dur + 0.15,
                           data={"say_id": turn.say_id, "turn_id": turn.turn_id}))
        return out

    if t == "agent.reply_end":
        turn.replying = False
        turn.tone = None
        turn.done = True
        out = [_mood(state, ctx)]
        if turn.group_open and turn.turn_id in ctx.ui.groups:
            out.append(cmd("timer.set", key=f"steps_clear:{turn.turn_id}",
                           delay_s=int(ctx.config.ui_steps.get("clear_after_reply_ms", 4000)) / 1000,
                           data={"group": turn.turn_id}))
        return out

    if t == "timer.fired":
        return _timer(state, p, ctx)
    return None


def _adopt(state: KernelState, t: str, p: dict[str, Any]) -> bool:
    sess, turn = state.session, state.turn
    if t not in ("agent.steps_plan", "agent.tool_call", "agent.reply_start") or not p.get("turn_id") or sess is None:
        return False
    if sess.mode in ("kernel_listen", "paused") or (state.incident_active and sess.mode != "reassure"):
        return False
    if turn is not None and (turn.turn_id == p["turn_id"] or not turn.done):
        return False
    state.turn = TurnState(turn_id=p["turn_id"], session_id=sess.id, text="")
    return True


def _timer(state: KernelState, p: dict[str, Any], ctx: Any) -> list[Command] | None:
    key = p.get("key", "")
    if key.startswith("say_end:"):
        say_id = key.split(":", 1)[1]
        return [ui({"type": "say_end", "id": say_id}), cmd("agent.say_done", say_id=say_id)]
    if key.startswith("steps_clear:"):
        gid = key.split(":", 1)[1]
        if gid in ctx.ui.groups:
            return [ui({"type": "steps_clear", "group": gid})]
    return None


def _vars_from_args(tool: str, args: dict[str, Any], ctx: Any = None) -> dict[str, Any]:
    v = dict(args)
    if tool == "get_route":
        dest = str(args.get("destination", ""))
        if not dest and ctx is not None:   # planned before its args are known: today's next appointment
            row = ctx.db.one("SELECT place FROM calendar_events WHERE substr(datetime,1,10)=? AND kind='medical' "
                             "AND datetime>=? ORDER BY datetime LIMIT 1", (ctx.now.date().isoformat(), ctx.now.isoformat()))
            dest = (row or {}).get("place") or ""
        for prefix in ("基督教", "香港"):
            if dest.startswith(prefix) and len(dest) > len(prefix) + 1:
                dest = dest[len(prefix):]
        v.setdefault("place_short", dest[:6])
    if tool == "send_message_to_family":
        v.setdefault("contact", args.get("contact", "阿珍"))
    if tool == "play_voice_message":
        v.setdefault("from_name", args.get("from_name", "阿珍"))
    return v
