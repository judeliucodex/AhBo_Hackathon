"""QA26 check 7: traces (INV-6) — after a fall and a conversation, every command has event_id +
correlation_id; /operator/api/trace?correlation_id=… returns the whole story."""
import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"
FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


c = httpx.Client(base_url=BASE, follow_redirects=False, timeout=30)
r = c.post("/login", data={"password": "qa-test-password", "next": "/operator/"})
check("login ok", r.status_code in (303, 200), str(r.status_code))

# ---- a conversation
r = c.post("/operator/api/trigger", json={"kind": "utterance", "text": "阿寶，我幾時食藥呀？"})
check("utterance trigger accepted", r.status_code == 200 and r.json().get("ok"), r.text[:120])
time.sleep(3)

# ---- a fall (operator source, the 模擬跌倒 path)
r = c.post("/operator/api/trigger", json={"kind": "fall"})
check("fall trigger accepted", r.status_code == 200 and r.json().get("ok"), r.text[:120])
time.sleep(45)                                   # let F1–F4, countdown, escalation, fake calls run

# ---- DB: INV-6 — every command carries event_id + correlation_id
from ahbo.core.db import open_db
db = open_db("/tmp/qa/ahbo.sqlite")
n_cmd = db.one("SELECT COUNT(*) n FROM commands")["n"]
bad = db.one("SELECT COUNT(*) n FROM commands WHERE event_id IS NULL OR correlation_id IS NULL")["n"]
check(f"all {n_cmd} commands have event_id + correlation_id (INV-6)", n_cmd > 0 and bad == 0, f"bad={bad}")
n_ev = db.one("SELECT COUNT(*) n FROM events")["n"]
bad_ev = db.one("SELECT COUNT(*) n FROM events WHERE correlation_id IS NULL")["n"]
check(f"all {n_ev} events carry a correlation_id", n_ev > 0 and bad_ev == 0, f"bad={bad_ev}")

# ---- the fall's whole story
inc = db.one("SELECT id FROM incidents ORDER BY opened_at DESC LIMIT 1")
inc = inc["id"] if inc else None
check("an incident exists", inc is not None, str(inc))
r = c.get("/operator/api/trace", params={"correlation_id": inc})
check("trace endpoint 200", r.status_code == 200, str(r.status_code))
t = r.json()
evs, cmds, spans = t.get("events") or [], t.get("commands") or [], t.get("spans") or []
types_ev = [e["type"] for e in evs]
types_cmd = [x["type"] for x in cmds]
check("trace returns events+commands+spans", bool(evs) and bool(cmds), f"{len(evs)} events, {len(cmds)} cmds, {len(spans)} spans")
check("story has the fall sensor event", "sensor.fall_suspected" in types_ev, str(types_ev[:6]))
check("story has the F-series clips", any(x["payload"].get("clip_id", "").startswith("F") for x in cmds
                                          if x["type"] == "device.play_fixed"),
      str([x["payload"].get("clip_id") for x in cmds if x["type"] == "device.play_fixed"]))
check("story has the family alert", "family.send" in types_cmd, str(types_cmd))
check("story has the phone call", "phone.call" in types_cmd, str(types_cmd))
check("every traced command matches the correlation_id",
      all(x["correlation_id"] == inc for x in cmds), str({x["correlation_id"] for x in cmds}))
check("every traced event matches the correlation_id",
      all(e["correlation_id"] == inc for e in evs), str({e["correlation_id"] for e in evs}))

# ---- the conversation's whole story
sess = db.one("SELECT id FROM sessions ORDER BY opened_at DESC LIMIT 1")
sess = sess["id"] if sess else None
check("a session exists", sess is not None, str(sess))
r = c.get("/operator/api/trace", params={"correlation_id": sess})
t2 = r.json()
ev2 = [e["type"] for e in (t2.get("events") or [])]
check("session trace has her utterance + agent reply",
      "user.utterance" in ev2 and "agent.reply_start" in ev2, str(ev2[:8]))
check("session trace has tool calls", "agent.tool_call" in ev2, str([e for e in ev2 if e.startswith("agent.")][:6]))
check("session trace has spans (§15 waterfall)", bool(t2.get("spans")), str(len(t2.get("spans") or [])))
spans_names = [s["name"] for s in (t2.get("spans") or [])]
print("  span names:", spans_names[:12])

# ---- a bogus correlation id: empty story, not an error
r = c.get("/operator/api/trace", params={"correlation_id": "inc_nope"})
check("unknown correlation id → empty trace, 200", r.status_code == 200 and not (r.json().get("commands") or []),
      r.text[:80])

# ---- unauthenticated trace is refused
r0 = httpx.get(BASE + "/operator/api/trace", params={"correlation_id": inc}, timeout=10)
check("trace without cookie refused", r0.status_code in (401, 403, 307), str(r0.status_code))

# ---- turn latency spans for doctor --latency (bonus)
n_lat = db.one("SELECT COUNT(*) n FROM spans WHERE name='turn.latency'")["n"]
print(f"  turn.latency spans recorded: {n_lat}")

print(f"\n{'ALL CHECKS PASS' if not FAILS else 'CHECK FAILURES: ' + '; '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
