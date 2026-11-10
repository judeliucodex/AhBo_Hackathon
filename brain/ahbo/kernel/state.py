"""Kernel state. Each flow owns one slice; reducers mutate only their own slice."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SessionState:
    id: str
    reason: str
    mode: str                      # companion | reminder | kernel_listen | reassure | paused
    text_only: bool
    opened_at: datetime
    last_activity: datetime
    agent_state: str = "listening"  # listening | thinking | speaking | idle
    user_speaking: bool = False
    deep_running: bool = False


@dataclass
class TurnState:
    turn_id: str
    session_id: str
    text: str
    group_open: bool = False                 # a step group exists on the face for this turn
    planned: list[str] = field(default_factory=list)   # step ids planned (pending) in order
    steps: dict[str, dict[str, Any]] = field(default_factory=dict)   # step_id -> {tool, state}
    call_steps: dict[str, str] = field(default_factory=dict)         # call_id -> step_id
    pending_cards: list[dict[str, Any]] = field(default_factory=list)
    tone: str | None = None                  # family | propose | calm | None
    tone_hint: str | None = None             # from tool results (voice note → family, activity → propose)
    replying: bool = False
    say_id: str | None = None
    done: bool = False


@dataclass
class KernelState:
    session: SessionState | None = None
    turn: TurnState | None = None
    last_session_closed_at: datetime | None = None
    family_active: bool = False              # a family voice note is being announced or played (§23.3 row 3)
    muted: bool = False
    # Slices owned by the flow reducers (kernel/reducers/*). Each reducer documents its own shape.
    incident: Any = None
    doses: dict[str, Any] = field(default_factory=dict)
    actions: dict[str, Any] = field(default_factory=dict)
    checkins: dict[str, Any] = field(default_factory=dict)
    concern: dict[str, Any] = field(default_factory=dict)
    arbiter: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def incident_active(self) -> bool:
        inc = self.incident
        if inc is None:
            return False
        st = inc.get("state") if isinstance(inc, dict) else getattr(inc, "state", None)
        return st not in (None, "IDLE", "CLOSED")
