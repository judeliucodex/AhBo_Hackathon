"""Memory wiring (AGENT_BRIEF): Kernel dispatchers and a Kernel subscription.

- `memory.session_closed` → run the post-session extractor in the background; reports `memory.extracted`.
- `memory.write {op, …}` → add_fact | remember | supersede_fact | confirm_fact | add_story | mark_story_used;
  reports `memory.write_result {op, ok, id?, reason?}`.
- every final `user.utterance` → repetition tagging/counting in the background; from the 3rd time the same
  question is asked today, a persistent `rep_<intent>` info card with the answer computed from Kernel data.
- `schedule.tick` → refresh/clear shown `rep_*` cards (the next dose moves on; a new day clears them) and run
  retention once per HKT day.
Dispatchers return immediately; all work is spawned, and results come back as Kernel events.
"""
from __future__ import annotations

import logging
from typing import Any

from ahbo.core.events import P2, Command, Event, ui

from . import extractor, life_stories, repetition, retention, store

log = logging.getLogger("ahbo.memory")

router = None
RETENTION_KEY = "memory_retention_date"


def _spawn(svc: Any, coro: Any, name: str) -> None:
    fn = getattr(svc, "spawn", None)
    if callable(fn):
        fn(coro)
    else:
        svc.kernel.spawn(coro, name=name)


def _clean(card: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in card.items() if v is not None}


class MemoryService:
    def __init__(self, svc: Any):
        self.svc = svc
        self.kernel = svc.kernel
        self.db = svc.db
        self.clock = svc.clock
        self._ticking = False

    # ---------------- extractor
    def on_session_closed(self, c: Command) -> None:
        sid = c.payload.get("session_id")
        if sid:
            _spawn(self.svc, self.extract(sid), "memory.extract")

    async def extract(self, session_id: str) -> dict[str, Any]:
        res = await extractor.run_extractor(self.db, self.clock, session_id)
        facts = res.get("facts") or {}
        await self.kernel.submit(Event(type="memory.extracted", source="system", correlation_id=session_id,
                                       payload={"session_id": session_id, "ok": res.get("ok", False),
                                                "reason": res.get("reason"),
                                                "facts_active": len(facts.get("active", [])),
                                                "facts_pending": len(facts.get("pending_review", []))}))
        return res

    # ---------------- memory.write
    def on_write(self, c: Command) -> None:
        _spawn(self.svc, self.write(c), "memory.write")

    async def write(self, c: Command) -> dict[str, Any]:
        p = dict(c.payload)
        op = p.get("op")
        try:
            if op in ("add_fact", "remember"):
                fid = store.remember(self.db, self.clock, p["fact"], p.get("category", "other"),
                                     source=p.get("source", "telegram"))
                res = {"ok": True, "id": fid}
            elif op == "supersede_fact":
                res = {"ok": True, "id": store.supersede_fact(self.db, self.clock, p["old_id"], p["fact"],
                                                              p.get("category"), source=p.get("source", "telegram"))}
            elif op == "confirm_fact":
                res = {"ok": store.confirm_fact(self.db, p["fact_id"], bool(p.get("accept", True)))}
            elif op == "add_story":
                res = {"ok": True, "id": life_stories.add_life_story(
                    self.db, self.clock, p["text"], title=p.get("title"), photo_id=p.get("photo_id"),
                    era=p.get("era"), topic=p.get("topic"), ok_to_reminisce=p.get("ok_to_reminisce"))}
            elif op == "mark_story_used":
                res = {"ok": life_stories.mark_story_used(self.db, self.clock, p["story_id"])}
            else:
                res = {"ok": False, "reason": "bad_op"}
        except store.MemoryRejected as e:
            res = {"ok": False, "reason": e.reason}
        except (KeyError, TypeError):
            res = {"ok": False, "reason": "bad_args"}
        if not res.get("ok") and "reason" not in res:
            res["reason"] = "not_found"
        await self.kernel.submit(Event(type="memory.write_result", source="system", correlation_id=c.correlation_id,
                                       payload={"op": op, "command_id": c.id, **res}))
        return res

    # ---------------- subscription
    def on_kernel(self, kind: str, obj: dict[str, Any]) -> None:
        if kind != "kernel_event":
            return
        t, p = obj.get("type"), obj.get("payload") or {}
        if t == "user.utterance" and p.get("final", True) and (p.get("text") or "").strip():
            _spawn(self.svc, self.on_utterance(p["text"]), "memory.repetition")
        elif t == "schedule.tick" and not self._ticking:
            self._ticking = True
            _spawn(self.svc, self.on_tick(), "memory.tick")

    async def on_utterance(self, text: str) -> dict[str, Any]:
        intent = await repetition.tag_intent(text)
        n = repetition.count(self.db, self.clock, intent)
        card = None
        if intent != "other" and n >= repetition.THRESHOLD:
            card = repetition.answer_card(self.db, self.clock, intent)
            if card:
                await self.kernel.emit(ui({"type": "card", "card": _clean(card)}, P2))
        return {"intent": intent, "count": n, "card": card}

    async def on_tick(self) -> None:
        try:
            await self.refresh_cards()
            today = self.clock.today().isoformat()
            if self.db.kv_get(RETENTION_KEY) != today:
                data_dir = getattr(getattr(self.svc, "settings", None), "data_dir", None)
                retention.run_retention(self.db, self.clock, data_dir)
                self.db.kv_set(RETENTION_KEY, today)
        finally:
            self._ticking = False

    async def refresh_cards(self) -> None:
        """Keep shown rep_* cards true: recompute from Kernel data; clear them on a new day."""
        today = self.clock.today().isoformat()
        cmds = []
        for cid, shown in list(self.kernel.ui.cards.items()):
            if not cid.startswith("rep_"):
                continue
            intent = cid[4:]
            if repetition.get_count(self.db, today, intent) < repetition.THRESHOLD:
                cmds.append(ui({"type": "card_clear", "id": cid}, P2))
                continue
            card = repetition.answer_card(self.db, self.clock, intent)
            if card is None:
                cmds.append(ui({"type": "card_clear", "id": cid}, P2))
            elif (card.get("label"), card.get("small")) != (shown.get("label"), shown.get("small")):
                cmds.append(ui({"type": "card", "card": _clean(card)}, P2))
        if cmds:
            await self.kernel.emit(*cmds)


def install(svc: Any) -> MemoryService:
    m = MemoryService(svc)
    svc.kernel.on("memory.session_closed", m.on_session_closed)
    svc.kernel.on("memory.write", m.on_write)
    svc.kernel.subscribe(m.on_kernel)
    return m
