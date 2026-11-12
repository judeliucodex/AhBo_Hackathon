"""QA21 check 2: OK/help matchers — variants, traps, help-wins; every wrong answer reported."""
from __future__ import annotations
import asyncio, sys
sys.path.insert(0, "/repo/brain/tests")
import pytest
from conftest import harness  # noqa: F401
from qa import watch, until, inc, ev
from ahbo.kernel.matchers import match_ok, match_redflag

OK_EXPECT = ["我冇事", "我冇事呀", "冇事", "冇事呀", "我没事", "我沒事", "沒事", "我無事", "冇問題", "沒問題",
             "ok", "OK", "OK啦", "ok喇", "我好好"]
NO_EXPECT = ["", "有冇事？", "你有冇事呀", "你有沒有事", "唔係冇事", "我唔係冇事呀", "點會冇事",
             "唔係沒事", "冇食", "我未食藥", "邊度有事呀", "你好嘢"]
HELP_EXPECT = ["救命", "好痛", "起唔到身", "起唔到", "跌親", "跌倒", "摔倒", "頭暈", "叫白車", "救護車",
               "幫我", "我冇事，不過好痛", "我冇事，但係起唔到身", "冇跌親", "我冇跌親"]


@pytest.mark.parametrize("text", OK_EXPECT)
def test_ok_variants(text):
    assert match_ok(text) == "ok", f"{text!r} → {match_ok(text)}"


@pytest.mark.parametrize("text", NO_EXPECT)
def test_traps_are_neither(text):
    assert match_ok(text) is None, f"{text!r} → {match_ok(text)} (must be neither ok nor help)"


@pytest.mark.parametrize("text", HELP_EXPECT)
def test_help_variants(text):
    assert match_ok(text) == "help", f"{text!r} → {match_ok(text)}"


# --- suspicious negated forms: reported whatever they do
@pytest.mark.parametrize("text", ["唔OK", "唔ok", "我唔係OK", "唔係ok", "not ok", "I am not ok", "冇事個頭"])
def test_negated_ok_forms(text):
    print("NEGATED-OK", repr(text), "->", match_ok(text))


# --- redflag context sanity for the same matchers module
def test_redflag_basics():
    assert match_redflag("阿寶我胸口痛")[0] == "emergency"
    assert match_redflag("要過數俾佢") == ("urgent", "scam", "要過數")
    assert match_redflag("我頭好暈") == ("urgent", "health", "頭好暈")
    assert match_redflag("今日天氣好好") is None


# --- in-flow: the same inputs decide a real fall incident
async def _to_awaiting(h, verdict="fine"):
    from ahbo.models.fakes import FAKE_VISION_FINE
    note = {"fine": FAKE_VISION_FINE, "problem": "可能有人跌咗喺地下"}[verdict]
    h.kernel.db.kv_set("fake_vision_note", note)
    await ev(h, "sensor.fall_suspected", "beacon", beacon_id="b1")
    assert await until(lambda: any(x.get("ui", {}).get("type") == "countdown" for x in h.device.sent))


async def _fresh(h):
    h.kernel.state.incident = None


@pytest.mark.parametrize("text,expect", [("我冇事", "RESOLVED_OK"), ("救命", "ESCALATING"),
                                         ("我冇事，不過好痛", "ESCALATING"), ("冇跌親", "ESCALATING")])
async def test_in_flow_decisions(harness, text, expect):
    h = harness; watch(h)
    await _to_awaiting(h)
    await h.say(text, wait=False)
    assert await until(lambda: (inc(h) or {}).get("state") in ("RESOLVED_OK", "ESCALATING"), 4)
    assert inc(h)["state"] == expect, f"{text!r} → {inc(h)['state']}"
    if expect == "RESOLVED_OK":
        h.kernel.cancel_timer(f"fall:clear:{inc(h)['id']}")
        h.kernel.cancel_timer(f"fall:decide:{inc(h)['id']}")


async def test_in_flow_trap_does_nothing(harness):
    h = harness; watch(h)
    await _to_awaiting(h)
    h.kernel.cancel_timer(f"fall:countdown:{inc(h)['id']}")
    await h.say("你有冇事呀？", wait=False)
    await asyncio.sleep(0.3)
    assert inc(h)["state"] == "AWAITING_OK", f"trap must not decide: {inc(h)['state']}"


# --- D1 repro: a negated OK resolves a real fall incident as "she's fine" (S1)
async def test_negated_ok_resolves_incident(harness):
    h = harness; watch(h)
    await _to_awaiting(h, "fine")
    h.kernel.cancel_timer(f"fall:countdown:{inc(h)['id']}")
    await h.say("唔OK", wait=False)                  # pure negated OK, no help word anywhere
    assert await until(lambda: (inc(h) or {}).get("state") in ("RESOLVED_OK", "ESCALATING"), 4)
    print("D1-REPRO 唔OK →", inc(h)["state"], "why:", inc(h).get("ok_how", "-"))
    fam = h.kernel.db.query("SELECT payload FROM commands WHERE type='family.send'")
    ph = h.kernel.db.query("SELECT payload FROM commands WHERE type='phone.call'")
    print("D1-REPRO family.send kinds:", ['"INFO"' in r["payload"] and "INFO" or "other" for r in fam],
          "| phone.call:", len(ph))
