"""Session reducer (§10.2, §24.2): opens and closes conversation sessions, keeps the agent mode.

Events: device.wake, session.open_request (internal), session.closed, user.utterance (activity),
agent.reply_end (activity), timer.fired(session_idle:*), operator.mute/unmute.
Writes its own table (`sessions`) through ctx.db; everything else is a command.

Mode is the Kernel's: companion (default), reminder, kernel_listen, reassure, paused (§5.1).
"""
from __future__ import annotations

from typing import Any

from ahbo.core.events import P1, P2, Command, Event, cmd, ui
from ahbo.core.ids import new_id

from ..state import KernelState, SessionState

IDLE_CLOSE_S = 30.0       # §24.2 rule 5
MAX_SESSION_S = 600.0     # 10 min


def open_session(state: KernelState, ctx: Any, reason: str, mode: str, *, priority: int = P2,
                 listening_mood: bool = True) -> list[Command]:
    """Open a session (or re-mode the open one). Shared by other reducers (fall, dose, checkin)."""
    text_only = ctx.settings.is_fake("livekit")
    out: list[Command] = []
    if state.session is not None:
        if state.session.mode != mode:
            state.session.mode = mode
            out.append(cmd("agent.mode", priority, mode=mode, session_id=state.session.id))
        state.session.last_activity = ctx.now
        return out
    sid = new_id("sess")
    state.session = SessionState(id=sid, reason=reason, mode=mode, text_only=text_only, opened_at=ctx.now,
                                 last_activity=ctx.now)
    ctx.db.execute("INSERT INTO sessions(id, opened_at, reason, mode, text_only) VALUES (?,?,?,?,?)",
                   (sid, ctx.now.isoformat(), reason, mode, int(text_only)))
    if listening_mood and mode in ("companion", "reminder") and not state.incident_active:
        out.append(ui({"type": "mood", "mood": "listening"}, priority))
    out.append(cmd("device.session_open", priority, session_id=sid, reason=reason, mode=mode, text_only=text_only))
    out.append(cmd("session.open", priority, session_id=sid, reason=reason, mode=mode, text_only=text_only))
    out.append(cmd("device.wake_enable", priority, on=False))
    out.append(cmd("timer.set", key=f"session_idle:{sid}", delay_s=IDLE_CLOSE_S, data={"session_id": sid}))
    out.append(cmd("timer.set", key=f"session_max:{sid}", delay_s=MAX_SESSION_S, data={"session_id": sid}))
    return out


def close_session(state: KernelState, ctx: Any, why: str = "idle") -> list[Command]:
    s = state.session
    if s is None:
        return []
    state.session = None
    state.last_session_closed_at = ctx.now
    ctx.db.execute("UPDATE sessions SET closed_at=? WHERE id=?", (ctx.now.isoformat(), s.id))
    out = [cmd("timer.cancel", key=f"session_idle:{s.id}"), cmd("timer.cancel", key=f"session_max:{s.id}"),
           cmd("device.session_close", P2, session_id=s.id, why=why),
           cmd("session.close", P2, session_id=s.id, why=why),
           cmd("memory.session_closed", P2, session_id=s.id)]
    if not state.incident_active:
        out.append(cmd("device.wake_enable", P2, on=True))
        if ctx.ui.mood == "listening":
            out.append(ui({"type": "mood", "mood": "idle"}))
    return out


def set_mode(state: KernelState, mode: str, priority: int = P1) -> list[Command]:
    if state.session is None or state.session.mode == mode:
        return []
    state.session.mode = mode
    return [cmd("agent.mode", priority, mode=mode, session_id=state.session.id)]


def _touch(state: KernelState, ctx: Any) -> list[Command]:
    s = state.session
    if s is None:
        return []
    s.last_activity = ctx.now
    if s.deep_running:   # don't close while a deep task runs (§24.2 rule 5): extend up to its 90 s limit
        return [cmd("timer.set", key=f"session_idle:{s.id}", delay_s=90.0, data={"session_id": s.id})]
    return [cmd("timer.set", key=f"session_idle:{s.id}", delay_s=IDLE_CLOSE_S, data={"session_id": s.id})]


def reduce(state: KernelState, ev: Event, ctx: Any) -> list[Command] | None:
    t, p = ev.type, ev.payload
    if t == "device.wake":
        if state.incident_active or state.muted:
            return None
        out = open_session(state, ctx, reason=p.get("trigger", "touch"), mode="companion")
        if not out and state.session is not None:    # already open: wake = listening again
            out = [ui({"type": "mood", "mood": "listening"})] + _touch(state, ctx)
        return out
    if t == "session.open_request":
        return open_session(state, ctx, reason=p.get("reason", "kernel"), mode=p.get("mode", "companion"),
                            listening_mood=p.get("listening_mood", True))
    if t in ("user.utterance", "agent.reply_end", "agent.reply_start", "agent.tool_call"):
        return _touch(state, ctx)
    if t == "agent.state" and state.session and p.get("session_id") == state.session.id:
        state.session.agent_state = p.get("state", state.session.agent_state)
        state.session.user_speaking = bool(p.get("user_speaking"))
        return _touch(state, ctx)
    if t == "session.closed":
        if state.session and p.get("session_id") in (None, state.session.id):
            return close_session(state, ctx, why=p.get("why", "agent"))
        return None
    if t == "timer.fired":
        key = p.get("key", "")
        data = p.get("data") or {}
        if key.startswith(("session_idle:", "session_max:")) and state.session \
                and data.get("session_id") == state.session.id:
            if state.incident_active and state.session.mode in ("kernel_listen", "reassure"):
                return _touch(state, ctx)     # never close the fall-check / reassure session on idle
            if state.turn and state.turn.replying:
                return _touch(state, ctx)
            return close_session(state, ctx, why="idle" if key.startswith("session_idle") else "max")
    if t == "operator.mute":
        state.muted = True
        return set_mode(state, "paused")
    if t == "operator.unmute":
        state.muted = False
        return set_mode(state, "companion")
    return None
