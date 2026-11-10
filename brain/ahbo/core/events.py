"""Event and Command models (CONTRACTS §4)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .clock import get_clock
from .ids import new_id

Source = Literal["beacon", "device", "telegram", "twilio", "agent", "deep", "operator", "scheduler",
                 "system", "action"]

P0, P1, P2, P3 = 0, 1, 2, 3


class Event(BaseModel):
    id: str = Field(default_factory=lambda: new_id("evt"))
    ts: str = Field(default_factory=lambda: get_clock().iso())
    type: str
    source: Source
    correlation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class Command(BaseModel):
    id: str = Field(default_factory=lambda: new_id("cmd"))
    event_id: str | None = None
    type: str
    target: str = ""
    priority: int = P2
    correlation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, _ctx: Any) -> None:
        if not self.target:
            self.target = self.type.split(".", 1)[0]


def ui(face_cmd: dict[str, Any], priority: int = P2, correlation_id: str | None = None) -> Command:
    """Shorthand for a `device.ui` command carrying one face command (§3)."""
    return Command(type="device.ui", priority=priority, correlation_id=correlation_id, payload={"ui": face_cmd})


def cmd(type_: str, priority: int = P2, correlation_id: str | None = None, **payload: Any) -> Command:
    return Command(type=type_, priority=priority, correlation_id=correlation_id, payload=payload)
