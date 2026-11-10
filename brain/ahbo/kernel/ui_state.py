"""ui_state (§23.3): the Kernel's model of what the face shows. Every `device.ui` command passes through
`observe()`, so this is always the exact mirror of the face and can replay it after a reconnect or reload
(§10.1, §23.9). It is also the only writer of `mood` (duplicate moods are dropped here).
"""
from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from ahbo.core.events import Command, ui

if TYPE_CHECKING:
    from .core import Kernel

MOODS = ("idle", "listening", "thinking", "curious", "warm", "alarm", "sleepy")
KIT_DEFAULT_ICON = {"med": "", "event": "🏥", "plan": "🚌", "voice_note": "🎙", "incident": "⚠️", "action": "📞",
                    "message": "💬", "story": "📖", "today": "☀️", "photo": "📷", "activity": "🎤", "info": "ℹ️"}
INTERNAL_CARD_KEYS = ("when_candidates",)


def public_card(card: dict[str, Any]) -> dict[str, Any]:
    """Strip Kernel-internal keys before a card goes to the face."""
    return {k: v for k, v in card.items() if k not in INTERNAL_CARD_KEYS and v is not None}


class UIState:
    def __init__(self, kernel: Kernel | None = None):
        self.kernel = kernel
        self.mood = "idle"
        self.cards: dict[str, dict[str, Any]] = {}
        self.groups: dict[str, dict[str, Any]] = {}   # id -> {title, steps: {id: {label, doneLabel, state}}}
        self.ok_button = False
        self.offline = False

    # ---------- the mirror ----------
    def observe(self, c: Command) -> bool:
        """Update the mirror from one outgoing `device.ui` command. Returns False to drop the command."""
        f = c.payload.get("ui") or {}
        t = f.get("type")
        if t == "mood":
            if f.get("mood") not in MOODS:
                return False
            if f["mood"] == self.mood:
                return False
            self.mood = f["mood"]
        elif t == "reset":
            self.mood, self.cards, self.groups, self.ok_button = "idle", {}, {}, False
        elif t == "card":
            card = f.get("card") or {}
            if card.get("id"):
                self.cards.pop(card["id"], None)
                self.cards[card["id"]] = copy.deepcopy(card)
        elif t == "card_update":
            card = self.cards.get(f.get("id", ""))
            if card:
                patch = {k: v for k, v in (f.get("patch") or {}).items() if k != "playing"}
                card.update(patch)
        elif t == "card_clear":
            if f.get("id"):
                self.cards.pop(f["id"], None)
            else:
                self.cards = {k: v for k, v in self.cards.items() if v.get("persistent")}
        elif t == "steps":
            g = f.get("group") or {}
            if g.get("id"):
                self.groups[g["id"]] = {"title": g.get("title"), "steps": {
                    s["id"]: {"label": s.get("label", ""), "doneLabel": s.get("doneLabel"),
                              "state": s.get("state", "pending")} for s in g.get("steps", [])}}
        elif t == "step":
            g = self.groups.get(f.get("group", ""))
            if g is not None:
                s = g["steps"].setdefault(f["id"], {"label": f.get("label", ""), "doneLabel": None, "state": "pending"})
                s["state"] = f.get("state", s["state"])
                if f.get("label"):
                    s["label"] = f["label"]
                elif s["state"] == "done" and s.get("doneLabel"):
                    s["label"] = s["doneLabel"]
        elif t == "steps_clear":
            if f.get("group"):
                self.groups.pop(f["group"], None)
            else:
                self.groups = {}
        elif t == "ok_button":
            self.ok_button = bool(f.get("show"))
        elif t == "offline":
            self.offline = bool(f.get("on"))
        return True

    def replay_commands(self) -> list[dict[str, Any]]:
        """Face commands that rebuild the current face from blank (§10.1 reconnect, §23.9 reload)."""
        out: list[dict[str, Any]] = [{"type": "reset"}]
        if self.mood != "idle":
            out.append({"type": "mood", "mood": self.mood})
        for card in self.cards.values():
            out.append({"type": "card", "card": public_card(card)})
        for gid, g in self.groups.items():
            group: dict[str, Any] = {"id": gid, "steps": [
                {"id": sid, "label": s["label"], "state": s["state"],
                 **({"doneLabel": s["doneLabel"]} if s.get("doneLabel") else {})}
                for sid, s in g["steps"].items()]}
            if g.get("title"):
                group["title"] = g["title"]
            out.append({"type": "steps", "group": group})
        if self.ok_button:
            out.append({"type": "ok_button", "show": True})
        if self.offline:
            out.append({"type": "offline", "on": True})
        return out

    def snapshot(self) -> dict[str, Any]:
        return {"mood": self.mood, "cards": [public_card(c) for c in self.cards.values()],
                "steps": [{"id": gid, **g} for gid, g in self.groups.items()], "ok_button": self.ok_button,
                "offline": self.offline}

    # ---------- persistence (restart recovery, §M9) ----------
    def save(self) -> None:
        if self.kernel is not None:
            self.kernel.db.kv_set("ui_state", {"mood": self.mood, "cards": self.cards, "groups": self.groups,
                                               "ok_button": self.ok_button, "offline": self.offline})

    def load(self) -> None:
        if self.kernel is None:
            return
        s = self.kernel.db.kv_get("ui_state")
        if s:
            self.mood = s.get("mood", "idle")
            self.cards = s.get("cards", {})
            self.groups = s.get("groups", {})
            self.ok_button = bool(s.get("ok_button"))
            self.offline = bool(s.get("offline"))

    # ---------- helpers for reducers ----------
    def has_cards(self, non_persistent_only: bool = True) -> bool:
        return any(not c.get("persistent") for c in self.cards.values()) if non_persistent_only else bool(self.cards)

    @staticmethod
    def mood_cmd(mood: str, priority: int = 2, corr: str | None = None) -> Command:
        return ui({"type": "mood", "mood": mood}, priority, corr)


def turn_mood(state: Any, *, tone: str | None, thinking: bool, listening: bool, quiet: bool) -> str:
    """§23.3 priority table for the conversational part (incident moods are set by the fall reducer)."""
    if state.incident_active:
        return "alarm"
    if state.family_active or tone == "family":
        return "warm"
    if thinking:
        return "thinking"
    if tone == "propose":
        return "curious"
    if listening:
        return "listening"
    if quiet and state.session is None:
        return "sleepy"
    return "idle"
