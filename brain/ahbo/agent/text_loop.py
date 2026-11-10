"""Text sessions (fake LiveKit, §26.3): her utterances arrive as text (browser iPad text box, operator, CLI) and
run through the same bridge, tools, filter, modes and steps as the LiveKit worker."""
from __future__ import annotations

import asyncio
from typing import Any

from ahbo.core.events import Command, Event
from ahbo.core.ids import new_id

from .bridge import AgentBridge
from .runner import TurnAgent


class TextLoop:
    def __init__(self, bridge: AgentBridge, agent: TurnAgent):
        self.bridge, self.agent, self.kernel = bridge, agent, bridge.kernel
        self.task: asyncio.Task | None = None
        self.kernel.on("session.close", self._on_close)
        self.kernel.on("agent.inject", self._on_inject)

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

    def _on_inject(self, c: Command) -> None:
        """agent.inject → speak a system line as a turn (§5.3); the arbiter decides when this is sent."""
        st = self.kernel.state
        if st.session is None:
            return
        p = c.payload
        line = f"[阿寶系統｜{p.get('kind', '提醒')}｜唔係王婆婆講嘅] {p.get('content', '')}。{p.get('instruction', '')}"
        turn_id = new_id("turn")
        self.task = asyncio.create_task(self.agent.run_turn(st.session.id, turn_id, line))
