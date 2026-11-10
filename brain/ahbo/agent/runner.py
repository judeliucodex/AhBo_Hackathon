"""TurnAgent: one conversational turn with an LLM tool loop (fake or real Flash), driving the Kernel through the
bridge. Used by the text loop; the LiveKit worker reuses the same prompts/tools/filter via /internal/*."""
from __future__ import annotations

import json
import logging
from typing import Any

from ahbo.core.adapters import FlashLLM

from .bridge import AgentBridge
from .filters.spoken import spoken_filter
from .tools import schemas

log = logging.getLogger("ahbo.agent")
MAX_ROUNDS = 6
HISTORY_TURNS = 12


class TurnAgent:
    def __init__(self, bridge: AgentBridge, llm: FlashLLM, system_prompt: Any = None):
        self.bridge, self.llm = bridge, llm
        self.system_prompt = system_prompt          # callable(session_id) -> str, or None
        self.history: dict[str, list[dict[str, Any]]] = {}

    def _system(self, session_id: str) -> str:
        if callable(self.system_prompt):
            return self.system_prompt(session_id)
        try:
            from .context import build_instructions   # §5.2, built by the agent component
            return build_instructions(self.bridge.kernel, session_id)
        except Exception:
            return "你係「阿寶」，一隻陪住王婆婆嘅機械寵物。只可以講廣東話。"

    def _silent(self) -> bool:
        s = self.bridge.kernel.state.session
        return s is not None and s.mode in ("kernel_listen", "paused")

    async def run_turn(self, session_id: str, turn_id: str, text: str) -> None:
        hist = self.history.setdefault(session_id, [])
        msgs: list[dict[str, Any]] = [{"role": "system", "content": self._system(session_id)}] + hist + \
            [{"role": "user", "content": text}]
        start = len(msgs) - 1
        planned, replied = False, False
        try:
            for _ in range(MAX_ROUNDS):
                s = self.bridge.kernel.state.session
                resp = await self.llm.chat(msgs, schemas(s.mode if s else "companion"), purpose="voice_turn",
                                           correlation_id=session_id)
                spoken = ""
                if resp.text:
                    fr = spoken_filter(resp.text)
                    spoken = fr.text
                    for q in fr.quality:
                        await self.bridge.quality(q, session_id, turn_id=turn_id, text=resp.text[:300])
                if resp.tool_calls and not planned and not spoken:
                    await self.bridge.steps_plan(session_id, turn_id, resp.plan or [tc.name for tc in resp.tool_calls])
                    planned = True
                if spoken and self._silent():
                    break                     # the Kernel took the floor (kernel_listen/paused): never talk over it
                if spoken:
                    say_id = await self.bridge.reply_start(session_id, turn_id, spoken)
                    replied = True
                    if say_id:
                        await self.bridge.wait_say(say_id)
                if not resp.tool_calls:
                    if spoken:
                        msgs.append({"role": "assistant", "content": spoken})
                    break
                am: dict[str, Any] = {"role": "assistant", "content": spoken or None, "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.name, "arguments": json.dumps(tc.args, ensure_ascii=False)}}
                    for tc in resp.tool_calls]}
                if resp.reasoning_content:
                    am["reasoning_content"] = resp.reasoning_content
                msgs.append(am)
                for tc in resp.tool_calls:
                    res = await self.bridge.call_tool(session_id, turn_id, tc.name, tc.args, tc.id)
                    if tc.name == "play_voice_message" and res.get("ok"):
                        await self.bridge.wait_playback(tc.args.get("id", ""))
                    msgs.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(res, ensure_ascii=False)})
        except Exception:
            log.exception("turn failed")
            if not replied and not self._silent():
                say_id = await self.bridge.reply_start(session_id, turn_id, "阿寶而家諗唔到嘢，等陣再試吓好嗎？")
                replied = True
                if say_id:
                    await self.bridge.wait_say(say_id)
        finally:
            await self.bridge.reply_end(session_id, turn_id)
        hist.extend(msgs[start:])
        # keep the last HISTORY_TURNS user turns
        users = [i for i, m in enumerate(hist) if m["role"] == "user"]
        if len(users) > HISTORY_TURNS:
            del hist[: users[-HISTORY_TURNS]]
