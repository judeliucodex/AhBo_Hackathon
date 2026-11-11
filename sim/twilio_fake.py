#!/usr/bin/env python3
"""Fake Twilio CLI (§26.3): a thin wrapper around `ahbo.links.twilio.FakeTwilio`.

Runs the fake channel on a throwaway in-memory DB (never the live stack): prints the outbox row with its TwiML
and follows the same status sequence the console shows (ringing → in-progress → completed over 6 s):

    python sim/twilio_fake.py --at "2026-11-20T15:00:00+08:00"
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
os.environ.setdefault("AHBO_DATA_DIR", "/tmp/ahbo-sim-twilio")

from ahbo.core.clock import FakeClock, set_clock  # noqa: E402
from ahbo.core.db import open_db  # noqa: E402
from ahbo.core.settings import load_settings  # noqa: E402
from ahbo.links import outbox  # noqa: E402
from ahbo.links.twilio import FakeTwilio, call_twiml  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    clock = FakeClock(args.at or "2026-11-20T15:00:00+08:00")
    set_clock(clock)
    db = open_db(":memory:")
    db.migrate()
    db.execute("INSERT INTO contacts(id,name,role,phone,verified,priority) VALUES "
               "('c_ecs','緊急中心（模擬）','emergency_centre_sim',NULL,1,0)")
    settings = load_settings()
    statuses: list[dict] = []

    async def submit(ev) -> None:
        statuses.append({"type": ev.type, "source": ev.source, "payload": ev.payload})

    kernel = SimpleNamespace(clock=clock, settings=settings, db=db, time_scale=args.scale, submit=submit,
                             spawn=lambda coro, name="bg": asyncio.ensure_future(coro), note=lambda ev: None)
    svc = SimpleNamespace(kernel=kernel, db=db, clock=clock, settings=settings, extra={})
    oid = outbox.record(db, ts=clock.iso(), channel="twilio", kind="CALL", to_contact="c_ecs", to_address="",
                        payload={"call_id": "call_demo", "clip": "P1", "incident_id": None}, fake=True,
                        status="queued", provider_id="call_demo")
    res = await FakeTwilio(svc).call(to_e164="", contact_id="c_ecs", call_id="call_demo",
                                     clip_url=f"{settings.public_base_url}/media/public/demo/P1.m4a",
                                     correlation_id=None, outbox_id=oid)
    await asyncio.sleep(7.0 * args.scale)
    row = outbox.get(db, oid)
    print(json.dumps({"send_result": res, "statuses": statuses, "expected_sequence": ["ringing", "in-progress",
                                                                                      "completed"],
                      "outbox_row": {**row, "payload": row["payload"], "buttons": row["buttons"]},
                      "twiml_sample": call_twiml("https://demo.invalid/media/public/demo/P1.m4a",
                                                 "https://demo.invalid/twilio/gather?call_id=call_demo")},
                     ensure_ascii=False, indent=2))
    db.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ah Bo fake Twilio (TwiML in the outbox + status sequence, §26.3)")
    ap.add_argument("--at", default=None, help="virtual clock, e.g. 2026-11-20T15:00:00+08:00")
    ap.add_argument("--scale", type=float, default=1.0, help="kernel time scale (tests use 0.05)")
    return asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
