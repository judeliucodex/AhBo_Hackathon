"""QA26 check 3: caps — push each cap over its limit with fake records; the feature refuses with a visible
warning; safety Twilio calls exempt up to 3 per incident (PRD §11)."""
import sys
import types

sys.path.insert(0, "/repo/brain/tests")

import pytest
from conftest import harness  # noqa: F401

from ahbo.core.events import Command, Event
from ahbo.core.jsonutil import loads
from ahbo.ops.caps import check_cap
from ahbo.trace.costs import record_cost, spend_today

FAILS: list[str] = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def wire_twilio(h):
    """Register the real phone.call dispatcher with the fake channel (mirrors links/wiring.py:41-49)."""
    from ahbo.links import twilio
    svc = types.SimpleNamespace(kernel=h.kernel, db=h.kernel.db, clock=h.kernel.clock,
                                settings=h.kernel.settings, config=h.kernel.config, extra={})
    tfl = twilio.FakeTwilio(svc)
    h.kernel.on("phone.call", twilio.phone_dispatcher(svc, tfl, fake=True))
    return svc


def wire_trace(h):
    from ahbo.trace.wiring import install
    install(types.SimpleNamespace(kernel=h.kernel, db=h.kernel.db))


def events_of(h, type_):
    rows = h.kernel.db.query("SELECT * FROM events WHERE type=? ORDER BY rowid", (type_,))
    for r in rows:
        r["payload"] = loads(r["payload"], {})
    return rows


async def settle(pred, timeout=6.0):
    import asyncio
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if pred():
            return True
        await asyncio.sleep(0.02)
    return False


# ---------------------------------------------------------------- twilio_calls (cap 5/day)
async def test_twilio_regular_call_refused_at_cap(harness):
    h = harness
    svc = wire_twilio(h)
    wire_trace(h)
    today = h.kernel.clock.today().isoformat()
    for i in range(5):                                   # 5 fake call records today (cap 5)
        record_cost(h.kernel.db, "twilio", None, {"calls": 1}, 0.0, correlation_id=f"ob_{i}",
                    purpose="phone_call", clock=h.kernel.clock)
    used = spend_today(h.kernel.db, h.kernel.clock)
    check("5 fake twilio records -> spend_today=5", used["twilio_calls"] == 5, str(used))

    await h.kernel.emit(Command(type="phone.call", target="phone", priority=0,
                                payload={"contact_id": "c_chun", "clip": "P1"}))
    ok = await settle(lambda: events_of(h, "phone.call_refused"))
    check("regular call refused when twilio_calls cap reached", ok)
    ref = events_of(h, "phone.call_refused")
    if ref:
        check("refusal reason is cap_reached", ref[-1]["payload"].get("reason") == "cap_reached",
              str(ref[-1]["payload"]))
    rows = h.kernel.db.query("SELECT * FROM outbox WHERE channel='twilio'")
    check("refused call wrote no outbox row", not rows, f"{len(rows)} rows")
    caps = events_of(h, "system.cap_exceeded")
    check("system.cap_exceeded warning recorded (console-visible)", caps and caps[-1]["payload"].get("name") == "twilio_calls",
          str(caps[-1]["payload"]) if caps else "no event")
    q = h.kernel.db.one("SELECT kind FROM quality WHERE kind='cap_exceeded'")
    check("quality row cap_exceeded recorded", q is not None)
    # spend counted the refused attempt? (usage is recorded after guard+cap: refused calls must NOT count)
    check("refused attempt not double-counted", spend_today(h.kernel.db, h.kernel.clock)["twilio_calls"] == 5)

    # ---- safety exemption: incident call allowed even at cap, up to 3 per incident
    inc = "inc_cap_test"
    for n in range(3):
        await h.kernel.emit(Command(type="phone.call", target="phone", priority=0, correlation_id=inc,
                                    payload={"contact_id": "c_chun", "clip": "P1", "incident_id": inc}))
        ok = await settle(lambda: len(h.kernel.db.query(
            "SELECT * FROM outbox WHERE channel='twilio' AND correlation_id=?", (inc,))) >= n + 1)
        check(f"safety call {n + 1}/3 allowed at cap (exempt)", ok)
    ok = await settle(lambda: events_of(h, "phone.call_refused") and
                      len(events_of(h, "phone.call_refused")) >= 2, timeout=4)
    if not ok:
        # 4th safety call for the same incident: give the kernel a moment
        await h.kernel.emit(Command(type="phone.call", target="phone", priority=0, correlation_id=inc,
                                    payload={"contact_id": "c_chun", "clip": "P1", "incident_id": inc}))
        ok = await settle(lambda: len(events_of(h, "phone.call_refused")) >= 2)
    check("4th safety call for the same incident refused", ok)
    if ok:
        check("4th refusal reason cap_reached (3/incident used)",
              events_of(h, "phone.call_refused")[-1]["payload"].get("reason") == "cap_reached",
              str(events_of(h, "phone.call_refused")[-1]["payload"]))
    total = len(h.kernel.db.query("SELECT * FROM outbox WHERE channel='twilio'"))
    check("exactly 3 safety outbox rows", total == 3, f"{total} outbox rows")


# ---------------------------------------------------------------- deep_tasks (cap 15/day, kernel reducer)
async def test_deep_tasks_cap_kernel_daily_cap(harness):
    h = harness
    db, clock, cfg = h.kernel.db, h.kernel.clock, h.kernel.config
    today = clock.today().isoformat()
    for i in range(15):
        db.execute("INSERT INTO deep_tasks(id,goal,constraints,status,session_id,started_at) "
                   "VALUES (?,?,?,?,?,?)", (f"deep_cap_{i}", "目標", "", "done", None, clock.iso()))
    n = db.one("SELECT COUNT(*) n FROM deep_tasks WHERE substr(started_at,1,10)=?", (today,))["n"]
    check("15 fake deep_tasks rows today", n == 15, str(n))
    # real kernel path: FakeFlash routes a planning ask to the deep_task tool → deep.start_request
    await h.say("阿寶，幫我計劃下星期做咩好啊？")
    r = await h.kernel.request(Event(type="deep.start_request", source="agent",
                                     payload={"goal": "計劃下星期", "constraints": ""}))
    check("deep.start_request over cap -> busy/daily_cap (CONTRACTS §6)",
          r is not None and r.get("ok") is False and r.get("reason") == "daily_cap", str(r))
    n2 = db.one("SELECT COUNT(*) n FROM deep_tasks WHERE substr(started_at,1,10)=?", (today,))["n"]
    check("no deep_tasks row added over cap", n2 == 15, str(n2))
    warns = events_of(h, "system.cap_exceeded")
    check("kernel daily_cap path emits NO cap warning (ops.caps never consulted here)",
          all(w["payload"].get("name") != "deep_tasks" for w in warns),
          f"{[w['payload'].get('name') for w in warns]}")
    # ops.caps path (used by the deep worker's own gate, real mode only):
    for i in range(15):   # ops.caps counts `costs` rows (the kernel reducer counts `deep_tasks` rows instead)
        record_cost(db, "fake", None, {}, 0.0, correlation_id=f"dt_{i}", purpose="deep_task", clock=clock)
    ok, used, cap = check_cap(db, clock, cfg, "deep_tasks")
    check("ops.caps counts costs rows: refuses at 15/15 (different source than the kernel gate)",
          not ok and used == 15.0 and cap == 15.0, f"ok={ok} {used}/{cap}")
    if h.kernel.settings.is_fake("models"):
        check("deep worker gate _cap_ok SKIPS caps when models faked", True)


# ---------------------------------------------------------------- llm_usd (cap $3/day)
async def test_llm_usd_cap_does_not_stop_conversation(harness):
    h = harness
    db, clock, cfg = h.kernel.db, h.kernel.clock, h.kernel.config
    wire_trace(h)
    for i in range(4):
        record_cost(db, "deepseek", "deepseek-v4.1-flash", {"tokens": 1000}, 1.0,
                    correlation_id=f"t{i}", purpose="chat", clock=clock)
    ok, used, cap = check_cap(db, clock, cfg, "llm_usd")
    check("check_cap refuses at $4.0/$3.0 (mechanics ok)", not ok and used == 4.0 and cap == 3.0, f"{used}/{cap}")
    warns = events_of(h, "system.cap_exceeded")
    check("llm_usd warning recorded via ops.caps", warns and warns[-1]["payload"].get("name") == "llm_usd",
          str(warns[-1]["payload"]) if warns else "none")
    await h.say("阿寶，我幾時食藥呀？")
    says = [x["ui"] for x in h.device.sent if x["type"] == "device.ui" and x["ui"].get("type") == "say_start"]
    check("conversation STILL runs over the llm_usd cap (no refusal anywhere)",
          bool(says) and "藥" in (says[-1].get("text") or ""), says[-1].get("text", "")[:40] if says else "no say")


# ---------------------------------------------------------------- sessions_per_day (cap 120/day)
async def test_sessions_per_day_cap_never_enforced(harness):
    h = harness
    db, clock, cfg = h.kernel.db, h.kernel.clock, h.kernel.config
    today = clock.today().isoformat()
    for i in range(120):
        db.execute("INSERT INTO sessions(id, opened_at, reason, mode) VALUES (?,?,?,?)",
                   (f"sess_cap_{i:03d}", clock.iso(), "fall_check", "kernel_listen"))
    used = spend_today(db, clock)
    check("120 fake sessions today", used["sessions"] == 120, str(used["sessions"]))
    ok, u, cap = check_cap(db, clock, cfg, "sessions_per_day")
    check("check_cap itself refuses the 121st session", not ok and u == 120 and cap == 120, f"{u}/{cap}")
    await h.say("阿寶，今日幾號呀？")
    says = [x for x in h.device.sent if x["type"] == "device.ui" and x["ui"].get("type") == "say_start"]
    check("a 121st session still opened (cap not enforced anywhere)", bool(says))
    n = db.one("SELECT COUNT(*) n FROM sessions WHERE substr(opened_at,1,10)=?", (today,))["n"]
    check(f"sessions now {n} (cap 120 exceeded, no refusal, no warning)", n >= 121, str(n))
    warns = events_of(h, "system.cap_exceeded")
    check("no sessions_per_day warning exists (feature never calls check_cap)",
          all(w["payload"].get("name") != "sessions_per_day" for w in warns))
    import subprocess
    g = subprocess.run(["grep", "-rn", "sessions_per_day", "/repo/brain/ahbo", "--include=*.py"],
                       capture_output=True, text=True).stdout
    enforced = [l for l in g.splitlines() if "check_cap" in l]
    check("grep confirms no check_cap('sessions_per_day') call site", not enforced, g.strip()[:200])


# ---------------------------------------------------------------- playwright_actions (cap 10/day)
async def test_playwright_actions_refused_at_cap(harness, monkeypatch):
    h = harness
    from ahbo.actions import wiring as actions_wiring
    svc = types.SimpleNamespace(kernel=h.kernel, db=h.kernel.db)
    actions_wiring.install(svc)
    wire_trace(h)
    clock = h.kernel.clock
    for i in range(10):
        record_cost(h.kernel.db, "playwright", None, {}, 0.0, correlation_id=f"act_{i}",
                    purpose="playwright_form", clock=clock)
    r = await h.kernel.request(Event(type="action.propose_request", source="agent",
                                     payload={"kind": "signup", "target": "act_opera_1500",
                                              "details": {"time": "15:00"}, "session_id": None}))
    check("action proposed", r and r.get("ok"), str(r))
    aid = (r or {}).get("action_id")
    r2 = await h.kernel.request(Event(type="action.confirm_request", source="agent",
                                      payload={"action_id": aid, "utterance": "好呀", "stt_confidence": 0.95}))
    check("action confirmed -> running", r2 and r2.get("ok") and r2.get("status") == "running", str(r2))
    await h.kernel.emit(Command(type="action.run", target="action", priority=2, payload={"action_id": aid}))

    def failed_cap():
        rows = h.kernel.db.query("SELECT * FROM events WHERE type='action.status' ORDER BY rowid")
        for row in rows:
            p = loads(row["payload"], {})
            if p.get("action_id") == aid and p.get("status") == "failed":
                return p
        return None
    p = None
    import asyncio
    end = asyncio.get_running_loop().time() + 6
    while asyncio.get_running_loop().time() < end and p is None:
        p = failed_cap()
        await asyncio.sleep(0.05)
    check("playwright action failed with daily_cap at 10/10", p is not None and p.get("detail", {}).get("reason") == "daily_cap",
          str(p))
    warns = events_of(h, "system.cap_exceeded")
    check("playwright_actions warning recorded as system.cap_exceeded",
          any(w["payload"].get("name") == "playwright_actions" for w in warns),
          str([w["payload"].get("name") for w in warns]))
    q = h.kernel.db.one("SELECT COUNT(*) n FROM quality WHERE kind='cap_exceeded'")["n"]
    check("quality row cap_exceeded recorded", q >= 1, str(q))


def pytest_sessionend(session):
    print(f"\n{'ALL CHECKS PASS' if not FAILS else 'CHECK FAILURES: ' + '; '.join(FAILS)}")
    session.exitstatus = 1 if FAILS else 0
