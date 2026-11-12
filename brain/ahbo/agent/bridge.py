"""AgentBridge: everything the agent (text loop in-process, or the LiveKit worker via /internal/*) does to the
Kernel (CONTRACTS §5). Every call becomes a Kernel event, so the trace explains every face change (INV-6)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ahbo.core.events import Command, Event
from ahbo.core.ids import new_id
from ahbo.kernel.core import Kernel

from .tools import ToolCtx, public, run_tool

log = logging.getLogger("ahbo.agent.bridge")
STT_LOW = 0.6     # below this an utterance is flagged `quality.stt_low_confidence` (the transcript is not logged)


class AgentBridge:
    def __init__(self, kernel: Kernel):
        self.kernel = kernel
        self._say: dict[str, asyncio.Future] = {}
        self._play: dict[str, asyncio.Future] = {}
        kernel.on("agent.say_done", self._on_say_done)
        kernel.on("agent.playback_done", self._on_playback_done)

    # ---- waiters ----
    def _fut(self, d: dict[str, asyncio.Future], key: str) -> asyncio.Future:
        if key not in d:
            d[key] = asyncio.get_running_loop().create_future()
        return d[key]

    def _on_say_done(self, c: Command) -> None:
        f = self._fut(self._say, c.payload["say_id"])
        if not f.done():
            f.set_result(c.payload)

    def _on_playback_done(self, c: Command) -> None:
        f = self._fut(self._play, c.payload["message_id"])
        if not f.done():
            f.set_result(c.payload)

    async def wait_say(self, say_id: str, timeout: float = 60.0) -> dict[str, Any] | None:
        try:
            return await asyncio.wait_for(asyncio.shield(self._fut(self._say, say_id)), timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._say.pop(say_id, None)

    async def wait_playback(self, message_id: str, timeout: float = 120.0) -> dict[str, Any] | None:
        try:
            return await asyncio.wait_for(asyncio.shield(self._fut(self._play, message_id)), timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._play.pop(message_id, None)

    # ---- §5 operations ----
    def _ev(self, type_: str, session_id: str | None, **payload: Any) -> Event:
        return Event(type=type_, source="agent", correlation_id=session_id, payload={"session_id": session_id, **payload})

    async def user_turn(self, session_id: str, turn_id: str, text: str, stt_confidence: float | None = None,
                        final: bool = True) -> dict[str, Any]:
        self.kernel.db.execute("INSERT INTO turns(id, session_id, ts, role, text, stt_confidence) VALUES (?,?,?,?,?,?)",
                               (new_id("t"), session_id, self.kernel.clock.iso(), "user", text, stt_confidence))
        await self.kernel.process(self._ev("user.utterance", session_id, turn_id=turn_id, text=text,
                                           stt_confidence=stt_confidence, final=final))
        if stt_confidence is not None and stt_confidence < STT_LOW:
            await self.quality("stt_low_confidence", session_id, turn_id=turn_id, stt_confidence=stt_confidence)
        st = self.kernel.state
        mode = st.session.mode if st.session else "companion"
        consumed = st.session is None or mode in ("kernel_listen", "paused") or (st.incident_active and mode != "reassure")
        out = {"ok": True, "consumed": consumed, "mode": mode, "text": text}
        line = None if consumed else self.llm_cap_line(text)
        if line:              # the LiveKit worker speaks this fixed line instead of calling the model (QA 26 D3)
            out.update(llm_capped=True, line=line)
        return out

    def llm_cap_line(self, text: str = "") -> str | None:
        """The daily `llm_usd` cap on the chat path (§11, QA 26 D3): over the cap her ordinary chat gets the fixed
        G1 line instead of a model call, and the cap warning (`system.cap_exceeded`) is raised once. Never applied to
        safety: an open incident, the reminder / reassure / kernel_listen / paused modes, an utterance the red-flag
        matcher flags, or a Kernel system line (injected reminders, family and concern messages)."""
        st = self.kernel.state
        s = st.session
        if st.incident_active or s is None or s.mode != "companion" or str(text).startswith("[阿寶系統"):
            return None
        k = self.kernel
        try:
            from ahbo.kernel.matchers import match_redflag
            if text and match_redflag(text, k.config):
                return None
            from ahbo.ops.caps import check_cap
            if check_cap(k.db, k.clock, k.config, "llm_usd")[0]:
                return None
        except Exception:
            log.exception("llm cap check failed; allowing the turn")
            return None
        return (k.config.fixed_lines or {}).get("G1") or "阿寶而家諗唔到嘢，等陣再試吓好嗎？"

    async def steps_plan(self, session_id: str, turn_id: str, tools: list[Any]) -> dict[str, Any]:
        await self.kernel.process(self._ev("agent.steps_plan", session_id, turn_id=turn_id, tools=tools))
        return {"ok": True}

    async def call_tool(self, session_id: str | None, turn_id: str | None, name: str, args: dict[str, Any] | None,
                        call_id: str | None = None) -> dict[str, Any]:
        call_id = call_id or new_id("call")
        await self.kernel.process(self._ev("agent.tool_call", session_id, turn_id=turn_id, call_id=call_id,
                                           name=name, args=args or {}))
        ctx = ToolCtx(kernel=self.kernel, session_id=session_id, turn_id=turn_id, call_id=call_id)
        res = await run_tool(ctx, name, args)
        await self.kernel.process(self._ev("agent.tool_result", session_id, turn_id=turn_id, call_id=call_id, name=name,
                                           ok=bool(res.get("ok")), result=public(res), cards=res.get("_cards") or [],
                                           step=res.get("_step") or {}, tone=res.get("_tone")))
        if res.get("reason") == "tool_error":
            await self.quality("tool_error", session_id, name=name, call_id=call_id, detail=res.get("_detail"))
        return public(res)

    async def quality(self, kind: str, session_id: str | None, **payload: Any) -> None:
        """Record a `quality.*` Kernel event (CONTRACTS §4.1): mandarin_drift, too_long, digits_spoken, tool_error,
        stt_low_confidence. Traced like any other event; never changes the face."""
        await self.kernel.process(self._ev(f"quality.{kind}", session_id, **payload))

    async def reply_start(self, session_id: str, turn_id: str, text: str, tone: str | None = None) -> str | None:
        self.kernel.db.execute("INSERT INTO turns(id, session_id, ts, role, text) VALUES (?,?,?,?,?)",
                               (new_id("t"), session_id, self.kernel.clock.iso(), "ahbo", text))
        await self.kernel.process(self._ev("agent.reply_start", session_id, turn_id=turn_id, text=text, tone=tone))
        t = self.kernel.state.turn
        return t.say_id if t and t.turn_id == turn_id else None

    async def reply_end(self, session_id: str, turn_id: str) -> dict[str, Any]:
        await self.kernel.process(self._ev("agent.reply_end", session_id, turn_id=turn_id))
        return {"ok": True}

    async def agent_state(self, session_id: str, state: str, user_speaking: bool = False) -> dict[str, Any]:
        await self.kernel.process(self._ev("agent.state", session_id, state=state, user_speaking=user_speaking))
        return {"ok": True}
