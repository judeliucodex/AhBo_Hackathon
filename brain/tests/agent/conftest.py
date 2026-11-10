"""Helpers for agent tests: a stub reducer that answers the Kernel request events (AGENT_BRIEF table), so tool
behaviour is tested independently of the Kernel flows (which another component builds)."""
from __future__ import annotations

from typing import Any

import pytest

from ahbo.agent.tools import ToolCtx

REQUESTS = ("dose.confirm_request", "dose.snooze_request", "agent.concern", "incident.status_request",
            "action.propose_request", "action.confirm_request", "deep.start_request", "family.message_request")


class StubKernelReplies:
    def __init__(self, kernel, answers: dict[str, Any] | None = None):
        self.seen: list[tuple[str, dict[str, Any]]] = []
        self.answers = answers or {}
        kernel.add_reducer("stub_requests", self.reduce)

    def reduce(self, state, ev, ctx):
        if ev.type in REQUESTS and ev.type in self.answers:
            self.seen.append((ev.type, dict(ev.payload)))
            a = self.answers[ev.type]
            ctx.reply(ev, a(ev.payload) if callable(a) else a)
        return None


@pytest.fixture
def stub(harness):
    def make(answers: dict[str, Any]) -> StubKernelReplies:
        return StubKernelReplies(harness.kernel, answers)
    return make


@pytest.fixture
def tctx(harness):
    return ToolCtx(kernel=harness.kernel, session_id="sess_test", turn_id="turn_test", call_id="call_test")
