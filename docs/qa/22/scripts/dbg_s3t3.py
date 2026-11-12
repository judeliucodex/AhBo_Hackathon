"""Debug the S3-turn3-signup flow in the eval harness with the browser + throwaway api reachable (v2)."""
import asyncio
import faulthandler
import json
import os
import sys

os.environ["AHBO_FAKE"] = "all"
os.environ["AHBO_PROFILE"] = "demo"
os.environ["PUBLIC_BASE_URL"] = "http://ahbo-qa22-api:8000"
os.environ["AHBO_BROWSER_WS"] = "ws://ahbo-qa22-browser:3000/"
os.environ["AHBO_ACTION_INTERNAL_BASE"] = "http://ahbo-qa22-api:8000"
os.environ["AHBO_DATA_DIR"] = "/tmp/qa-dbg"

import logging  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s", stream=sys.stderr)
faulthandler.dump_traceback_later(150, file=sys.stderr)


def out(*a):
    print(*a, file=sys.stderr, flush=True)


from ahbo.evals.harness import EvalSystem
from ahbo.core.jsonutil import loads as jloads  # noqa: E402


async def main() -> None:
    s = await EvalSystem.create()
    s.clock.set_time("08:30")
    s.kernel.load()
    await s.say("阿寶，我今日有咩安排呀？")
    await s.settle()
    await s.say("咁我下晝好悶喎……")
    await s.settle()
    out("ACTIONS after offer:", json.dumps(s.db.query("SELECT id,state,method FROM actions"), ensure_ascii=False))
    await s.say("好呀")
    await s.settle()
    out("ACTIONS after yes:", json.dumps(s.db.query("SELECT id,state,method FROM actions"), ensure_ascii=False))
    out("action.run commands:", json.dumps(s.commands("action.run"), ensure_ascii=False))
    for i in range(40):
        await asyncio.sleep(1.0)
        sts = [jloads(r["payload"], {}) for r in s.db.query("SELECT payload FROM events WHERE type='action.status'")]
        rows = s.db.query("SELECT id,state FROM actions")
        if sts and rows and rows[-1]["state"] in ("done", "failed", "needs_human"):
            out(f"final after {i}s")
            break
    out("ACTIONS:", json.dumps(s.db.query("SELECT id,state,method,details FROM actions"), ensure_ascii=False))
    out("STATUS EVENTS:", json.dumps([jloads(r["payload"], {}) for r in
                                      s.db.query("SELECT payload FROM events WHERE type='action.status'")],
                                     ensure_ascii=False))
    out("OUTBOX:", json.dumps([{k: r[k] for k in ("channel", "kind", "to_contact", "status")} for r in s.outbox()],
                              ensure_ascii=False))
    out("LINES:", json.dumps(s.lines(), ensure_ascii=False))
    out("WIRING ERRORS:", json.dumps(dict(s.wiring_errors), ensure_ascii=False))
    try:
        await asyncio.wait_for(s.close(), timeout=15)
        out("closed cleanly")
    except asyncio.TimeoutError:
        out("close() TIMED OUT (hung)")
        os._exit(3)


asyncio.run(main())
out("process exit", 0)
os._exit(0)
