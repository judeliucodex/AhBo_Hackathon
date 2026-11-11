#!/usr/bin/env python3
"""Fake Telegram CLI (§26.3): a thin wrapper around `ahbo.links.telegram.FakeTelegram` and the outbox renderer.

The fake classes live in `ahbo.links` (one source of truth). This CLI runs them on a throwaway in-memory DB —
it never touches the live stack or the real data dir — so you can see exactly what a fake delivery looks like
(outbox row with buttons, like the console's Outbox panel):

    python sim/telegram_fake.py --kind ALERT --text "王婆婆懷疑跌倒" --button handled,resolved
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from types import SimpleNamespace
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for p in (str(_ROOT / "brain"), str(_ROOT)):      # make `ahbo` importable when run from a checkout
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("AHBO_FAKE", "all")
os.environ.setdefault("AHBO_PROFILE", "demo")
os.environ.setdefault("PUBLIC_BASE_URL", "https://demo.invalid")
os.environ.setdefault("AHBO_DATA_DIR", "/tmp/ahbo-sim-telegram")

from ahbo.core.clock import FakeClock, set_clock  # noqa: E402
from ahbo.core.db import open_db  # noqa: E402
from ahbo.core.settings import load_settings  # noqa: E402
from ahbo.links.family import compose, render_buttons  # noqa: E402
from ahbo.links.telegram import FakeTelegram  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    clock = FakeClock(args.at or "2026-11-20T15:00:00+08:00")
    set_clock(clock)
    db = open_db(":memory:")
    db.migrate()
    db.execute("INSERT INTO contacts(id,name,role,telegram_chat_id,verified,priority) VALUES "
               "('c_chun','阿珍','family','100000001',1,1)")
    settings = load_settings()
    kernel = SimpleNamespace(clock=clock, settings=settings, db=db)
    svc = SimpleNamespace(kernel=kernel, db=db, clock=clock, settings=settings)
    oid = f"ob_demo_{args.kind.lower()}"
    buttons = [b for b in (args.button.split(",") if args.button else [])]
    db.execute(
        "INSERT INTO outbox(id, ts, channel, kind, to_contact, to_address, payload, buttons, status, fake, "
        "provider_id, updated_at) VALUES (?,?,?,?,?,?,?,?,?,1,'tgfake0001',?)",
        (oid, clock.iso(), "telegram", args.kind, "c_chun", "100000001",
         json.dumps({"text": compose(args.kind, args.text)}, ensure_ascii=False),
         json.dumps(render_buttons(buttons, oid), ensure_ascii=False), "sent", clock.iso()))
    res = await FakeTelegram(svc).send(kind=args.kind, contact_id="c_chun", chat_id="100000001",
                                       text=compose(args.kind, args.text), correlation_id=None)
    row = db.one("SELECT * FROM outbox WHERE id=?", (oid,))
    print(json.dumps({"send_result": res, "outbox_row": {**row, "payload": json.loads(row["payload"]),
                                                         "buttons": json.loads(row["buttons"])}},
                     ensure_ascii=False, indent=2))
    db.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ah Bo fake Telegram (outbox-only delivery, §26.3)")
    ap.add_argument("--kind", default="ALERT", choices=("ALERT", "NOTIFY", "INFO", "DIGEST", "HANDOFF"))
    ap.add_argument("--text", default="王婆婆懷疑跌倒，冇回應。")
    ap.add_argument("--button", default="handled,resolved", help="comma-separated button ids")
    ap.add_argument("--at", default=None, help="virtual clock, e.g. 2026-11-20T15:00:00+08:00")
    return asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
