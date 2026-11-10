"""Voice-note playback (S1, §9.2): card waveform + device playback + family_active (mood warm, §23.3 row 3).

Events: voice.play_request {message_id, say_id?, card_id?}, device.playback_done {message_id, say_id}.
"""
from __future__ import annotations

from typing import Any

from ahbo.core.events import P2, Command, Event, cmd, ui

from ..state import KernelState


def reduce(state: KernelState, ev: Event, ctx: Any) -> list[Command] | None:
    t, p = ev.type, ev.payload
    playing = state.extra.setdefault("voice_playing", {})
    if t == "voice.play_request":
        vm = ctx.db.one("SELECT * FROM voice_messages WHERE id=?", (p["message_id"],))
        if not vm:
            return [cmd("agent.playback_done", message_id=p["message_id"], ok=False, reason="not_found")]
        say_id = p.get("say_id") or f"say_{vm['id']}_{int(ctx.now.timestamp())}"
        card_id = p.get("card_id") or f"vn_{vm['id']}"
        playing[say_id] = {"message_id": vm["id"], "card_id": card_id}
        state.family_active = True
        out: list[Command] = []
        if card_id in ctx.ui.cards:
            out.append(ui({"type": "card_update", "id": card_id, "patch": {"playing": True}}))
        dur = float(vm.get("duration_s") or 0) or max(1.2, len(vm.get("transcript") or "") / 5.2)
        out.append(cmd("device.play_voice_message", p.get("priority", P2), message_id=vm["id"],
                       url=f"/media/voice/{vm['id']}", say_id=say_id, from_name=vm.get("from_name") or "家人",
                       text=vm.get("transcript") or None, duration_s=round(dur, 2)))
        # Never hang if no device plays it (degraded mode): give up after duration + 5 s
        out.append(cmd("timer.set", key=f"voice_timeout:{say_id}", delay_s=dur + 5.0, data={"say_id": say_id}))
        return out
    if t in ("device.playback_done", "timer.fired"):
        say_id = p.get("say_id") or (p.get("data") or {}).get("say_id")
        if t == "timer.fired" and not p.get("key", "").startswith("voice_timeout:"):
            return None
        info = playing.pop(say_id, None) if say_id else None
        if not info:
            return None
        state.family_active = bool(playing)
        ctx.db.execute("UPDATE voice_messages SET played_at=? WHERE id=?", (ctx.now.isoformat(), info["message_id"]))
        out = [cmd("timer.cancel", key=f"voice_timeout:{say_id}")]
        if info["card_id"] in ctx.ui.cards:
            out.append(ui({"type": "card_update", "id": info["card_id"], "patch": {"playing": False}}))
        out.append(cmd("agent.playback_done", message_id=info["message_id"], say_id=say_id,
                       ok=t == "device.playback_done"))
        return out
    return None
