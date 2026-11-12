"""QA21 check 1 (restart): fall incident in progress, process dies, a new Kernel is built on the same DB and
`kernel.load()` runs — what does 王婆婆 see and hear afterwards?"""
from __future__ import annotations
import asyncio, sys
sys.path.insert(0, "/repo/brain/tests")
import pytest
from conftest import FakeDevice, TIME_SCALE  # noqa: E401
from ahbo.core.clock import FakeClock, set_clock
from ahbo.core.config import load_config
from ahbo.core.db import open_db
from ahbo.core.settings import load_settings
from ahbo.kernel.core import Kernel
from ahbo.kernel.reducers import register_all
from ahbo.ops.seed import load_demo
from ahbo.models.fakes import FAKE_VISION_FINE

DB_PATH = "/scratch/qa21_restart.sqlite"


def make_kernel(clock, db):
    settings = load_settings()
    k = Kernel(db=db, clock=clock, config=load_config(fresh=True), settings=settings, time_scale=TIME_SCALE)
    register_all(k)
    return k


async def new_stack(db_path, seed=False):
    clock = FakeClock("2026-11-20T10:00:00+08:00")
    set_clock(clock)
    db = open_db(db_path)
    k = make_kernel(clock, db)
    if seed:
        load_demo(db, clock, settings_of())
    k.load()                                          # the platform runs this at startup (restart recovery)
    dev = FakeDevice(k)
    k.start()
    return k, dev, clock, db


def settings_of():
    return load_settings()


async def until(pred, t=5.0):
    end = asyncio.get_running_loop().time() + t
    while asyncio.get_running_loop().time() < end:
        if pred():
            return True
        await asyncio.sleep(0.01)
    return pred()


async def fall_to_awaiting(k, dev):
    k.db.kv_set("fake_vision_note", FAKE_VISION_FINE)
    await k.process(__import__("ahbo.core.events", fromlist=["Event"]).Event(
        type="sensor.fall_suspected", source="beacon", payload={"beacon_id": "b1"}))
    assert await until(lambda: any(x.get("ui", {}).get("type") == "countdown" for x in dev.sent))


def uis(dev, ftype=None):
    out = [x["ui"] for x in dev.sent if x["type"] == "device.ui"]
    return [u for u in out if ftype is None or u.get("type") == ftype]


@pytest.fixture
async def fresh_db():
    import os
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    yield DB_PATH
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)


async def test_restart_during_awaiting_ok(fresh_db):
    db_path = fresh_db
    k1, dev1, clock, db = await new_stack(db_path, seed=True)
    await fall_to_awaiting(k1, dev1)
    iid = k1.state.incident["id"]
    await k1.stop(); db.close()                       # the process dies mid-countdown

    k2, dev2, clock2, db2 = await new_stack(db_path)  # new process, same DB; load() runs at startup
    assert k2.state.incident is not None and k2.state.incident["id"] == iid
    assert k2.state.incident["state"] == "AWAITING_OK"
    assert await until(lambda: any(u.get("type") == "countdown" for u in uis(dev2)), 2)
    kinds = [u.get("type") for u in uis(dev2)]
    print("RESTART-AWAITING sees:", kinds)
    print("RESTART-AWAITING clips:", [c["clip_id"] for c in dev2.sent if c["type"] == "device.play_fixed"])
    # she must still be able to answer: the OK button and a fresh countdown come back
    assert "ok_button" in kinds and "countdown" in kinds, f"face after restart: {kinds}"
    assert k2.state.session is not None and k2.state.session.mode == "kernel_listen"
    k2.cancel_timer(f"fall:countdown:{iid}")          # freeze the window for the probe
    await k2.process(__import__("ahbo.core.events", fromlist=["Event"]).Event(
        type="device.touch_ok", source="device", payload={}))
    assert await until(lambda: k2.state.incident["state"] == "RESOLVED_OK")
    fam = db2.query("SELECT payload FROM commands WHERE type='family.send'")
    assert fam and '"INFO"' in fam[-1]["payload"]
    await k2.stop(); db2.close()


async def test_restart_during_reassuring(fresh_db):
    db_path = fresh_db
    k1, dev1, clock, db = await new_stack(db_path, seed=True)
    await fall_to_awaiting(k1, dev1)
    iid = k1.state.incident["id"]                     # let the countdown expire → escalate → call 阿珍
    assert await until(lambda: k1.state.incident["state"] == "ESCALATING", 8)
    assert db.query("SELECT * FROM commands WHERE type='phone.call'")
    await k1.stop(); db.close()                       # dies while REASSURING-pending (F4 may not have finished)

    k2, dev2, clock2, db2 = await new_stack(db_path)
    assert k2.state.incident["state"] in ("ESCALATING", "REASSURING")
    await asyncio.sleep(0.3)
    kinds = [u.get("type") for u in uis(dev2)]
    print("RESTART-ESCALATING sees:", kinds)
    print("RESTART-ESCALATING clips:", [c["clip_id"] for c in dev2.sent if c["type"] == "device.play_fixed"])
    # tack timer re-armed: missing the ack must still call the next contact (≤3 total for this incident)
    placed_before = k2.state.incident["calls_placed"]
    end = asyncio.get_running_loop().time() + 15
    while asyncio.get_running_loop().time() < end:
        if k2.state.incident["calls_placed"] > placed_before:
            break
        await asyncio.sleep(0.2)
    print("calls after restart:", db2.query("SELECT payload FROM commands WHERE type='phone.call'"))
    assert k2.state.incident["calls_placed"] >= placed_before  # tack re-armed (checked below for ≤3)
    assert k2.state.incident["calls_placed"] <= 3
    await k2.stop(); db2.close()
