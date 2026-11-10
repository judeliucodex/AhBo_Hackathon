"""Text sessions (fake LiveKit, §26.3): her utterances arrive as text (browser iPad text box, operator, CLI) and
run through the same bridge, tools, filter, modes and steps as the LiveKit worker."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ahbo.core.events import Command, Event
from ahbo.core.ids import new_id

from . import prompts
from .bridge import AgentBridge
from .runner import TurnAgent

log = logging.getLogger("ahbo.agent.text_loop")
SILENT_MODES = ("kernel_listen", "paused")


class TextLoop:
    def __init__(self, bridge: AgentBridge, agent: TurnAgent):
        self.bridge, self.agent, self.kernel = bridge, agent, bridge.kernel
        self.task: asyncio.Task | None = None
        self.kernel.on("session.close", self._on_close)
        self.kernel.on("agent.inject", self._on_inject)
        self.kernel.on("agent.pause", self._on_pause)
        self.kernel.on("agent.resume", self._on_resume)
        self.paused = False

    async def utterance(self, text: str, *, stt_confidence: float = 1.0) -> dict[str, Any]:
        st = self.kernel.state
        if st.session is None:
            await self.kernel.process(Event(type="device.wake", source="device",
                                            payload={"trigger": "touch", "implicit": True}))
        if st.session is None:
            return {"ok": False, "reason": "no_session"}
        turn_id = new_id("turn")
        res = await self.bridge.user_turn(st.session.id, turn_id, text, stt_confidence)
        if res["consumed"]:
            return res
        if self.task and not self.task.done():
            self.task.cancel()          # barge-in: the new words win (§24.6)
        self.task = asyncio.create_task(self.agent.run_turn(st.session.id, turn_id, text))
        return {**res, "turn_id": turn_id}

    async def wait_idle(self, timeout: float = 60.0) -> None:
        if self.task:
            await asyncio.wait_for(asyncio.shield(self.task), timeout)

    def _on_close(self, c: Command) -> None:
        if self.task and not self.task.done():
            self.task.cancel()

    def _on_pause(self, c: Command) -> None:
        """agent.pause (the Kernel plays fixed audio / the operator muted): stop the current turn now (§5.1)."""
        self.paused = True
        if self.task and not self.task.done():
            self.task.cancel()

    def _on_resume(self, c: Command) -> None:
        """agent.resume {mode}: the Kernel has already set the session mode; new turns may run again."""
        self.paused = False

    def _on_inject(self, c: Command) -> None:
        """agent.inject → speak a system line as its own turn (§5.3); the arbiter decides when this is sent.
        The line is `[阿寶系統｜<TYPE>｜唔係王婆婆講嘅] <content>。<instruction>。` (prompts.system_line). It waits for a
        running turn to finish (never talks over it) and is dropped while the agent must stay silent."""
        st = self.kernel.state
        if st.session is None or st.session.mode in SILENT_MODES:
            return
        p = c.payload
        sid = st.session.id
        if p.get("session_id") not in (None, sid):
            return
        line = prompts.system_line(p.get("kind", "提醒"), p.get("content", ""), p.get("instruction", ""))
        turn_id = new_id("turn")
        prev = self.task

        async def run() -> None:
            if prev is not None and not prev.done():
                try:
                    await asyncio.wait_for(asyncio.shield(prev), 60)
                except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                    pass
            s = self.kernel.state.session
            if s is None or s.id != sid or s.mode in SILENT_MODES:
                return
            # the turn reducer adopts a turn the agent starts itself (its first steps_plan/tool_call/reply_start)
            await self.agent.run_turn(sid, turn_id, line)

        self.task = asyncio.create_task(run())
