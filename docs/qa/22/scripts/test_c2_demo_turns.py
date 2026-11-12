"""QA22 check 2: the four demo turns through the eval harness (wiring + browser for turn 3)."""
import faulthandler
import json
import sys

faulthandler.dump_traceback_later(90, file=sys.stderr)

import pytest

sys.path.insert(0, "/repo")
import threading, time

def _watchdog():
    time.sleep(120)
    import os
    os._exit(7)

threading.Thread(target=_watchdog, daemon=True).start()

from ahbo.evals.harness import EvalSystem  # noqa: E402


@pytest.fixture
async def ev():
    s = await EvalSystem.create()
    try:
        yield s
    finally:
        await s.close()


def ui_of(s):
    return s.device.ui()


def mood_seq(s):
    return [u.get("mood") for u in ui_of(s) if u.get("type") == "mood"]


def cards(s):
    return [u["card"] for u in ui_of(s) if u.get("type") == "card" and u.get("card")]


def steps_cmds(s):
    return [u for u in ui_of(s) if u.get("type") in ("steps", "step", "steps_clear")]


def says(s):
    return [u for u in ui_of(s) if u.get("type") in ("say_start", "say_end")]


def says_text(s):
    return [u["text"] for u in says(s) if u.get("type") == "say_start" and u.get("from") != "family"]


async def test_S1(ev):
    s = ev
    s.clock.set_time("09:00")
    s.kernel.load()
    await s.say("阿寶，今日有冇人搵過我呀？")
    await s.settle()
    assert says_text(s)[-1] == "有呀！阿珍今朝留咗段錄音俾你，我而家播俾你聽。", says_text(s)
    groups = [u["group"] for u in steps_cmds(s) if u.get("type") == "steps"]
    assert groups and groups[0]["steps"][0]["label"] == "檢查緊訊息…", groups
    dones = [u for u in steps_cmds(s) if u.get("type") == "step" and u.get("state") == "done"]
    assert dones and dones[0].get("label") == "搵到阿珍嘅語音訊息", dones
    cs = cards(s)
    assert any(c["kind"] == "voice_note" and c.get("label") == "阿珍嘅錄音" for c in cs), cs
    sent = s.device.sent
    pvm = next(x for x in sent if x["type"] == "device.play_voice_message")
    assert pvm.get("message_id") == "vm_demo" and pvm.get("from_name") == "阿珍", pvm
    assert pvm.get("text") == "媽，記得食飯呀！星期日我帶阿明嚟探你。", pvm   # the iPad turns this into the family say_start
    await s.wait(4.0)          # let the voice note finish (playback is device-paced, not turn-paced)
    await s.settle()
    ups = [u for u in ui_of(s) if u.get("type") == "card_update" and (u.get("patch") or {}).get("playing")]
    assert ups and ups[0].get("id") == "vn_vm_demo", ups
    downs = [u for u in ui_of(s) if u.get("type") == "card_update" and (u.get("patch") or {}).get("playing") is False]
    assert downs, downs
    ms = mood_seq(s)
    assert ms[0] == "listening", ms            # wake
    assert "thinking" in ms and "warm" in ms, ms
    assert ms[-1] == "idle", ms
    row = s.db.one("SELECT played_at FROM voice_messages WHERE id='vm_demo'")
    assert row and row["played_at"], row


async def test_S2(ev):
    s = ev
    s.clock.set_time("10:00")
    s.kernel.load()
    await s.say("阿寶，我幾時食藥呀？")
    await s.settle()
    assert says_text(s)[-1] == "兩個鐘之後，十二點正。有血壓藥、糖尿藥、膽固醇藥，同埋薄血丸。到時我會提你㗎。", says_text(s)
    groups = [u["group"] for u in steps_cmds(s) if u.get("type") == "steps"]
    assert groups and groups[0]["steps"][0]["label"] == "查緊藥物時間表…", groups
    dones = [u for u in steps_cmds(s) if u.get("type") == "step" and u.get("state") == "done"]
    assert dones and dones[0].get("label") == "下一次：十二點正，四隻藥", dones
    cs = cards(s)
    assert any(c.get("id") == "next_dose" and c.get("kind") == "event" and c.get("label") == "12:00 食藥"
               and c.get("small") == "兩個鐘之後" for c in cs), cs
    for med in ("血壓藥", "糖尿藥", "膽固醇藥", "薄血丸"):
        assert any(c.get("kind") == "med" and c.get("label") == med for c in cs), cs
    ms = mood_seq(s)
    assert ms[0] == "listening" and "thinking" in ms, ms
    seq = [(u.get("type"), u.get("mood") or (u.get("card") or {}).get("id") or u.get("state")) for u in ui_of(s)]
    idx_done = next(i for i, (t, x) in enumerate(seq) if t == "step" and x == "done")
    idx_caption = next(i for i, (t, _) in enumerate(seq) if t == "say_start")
    last_card = max(i for i, (t, _) in enumerate(seq) if t == "card")
    assert idx_done < last_card < idx_caption, seq   # step ✓ → mood → cards → caption (D-7)


async def test_S3_turns(ev):
    s = ev
    s.clock.set_time("08:30")
    s.kernel.load()
    # ---- turn 1
    await s.say("阿寶，我今日有咩安排呀？")
    await s.settle()
    assert says_text(s)[-1] == ("十點半你要去聯合醫院覆診，我已經查好路線，九點半喺樓下搭巴士就得。"
                                "十二點半同阿珍飲茶。"), says_text(s)
    cs = cards(s)
    trip = {(c.get("kind"), c.get("label"), c.get("small")) for c in cs}
    assert ("event", "10:30 覆診", "聯合醫院") in trip, trip
    assert ("plan", "9:30 出門", "樓下巴士站") in trip, trip
    assert ("event", "12:30 同阿珍飲茶", "觀塘") in trip, trip
    labels = [st["label"] for u in steps_cmds(s) if u.get("type") == "steps" for st in u["group"]["steps"]]
    assert "睇緊今日日程…" in labels and "查緊去聯合醫院嘅巴士路線…" in labels, labels
    # ---- turn 2
    await s.say("咁我下晝好悶喎……")
    await s.settle()
    assert says_text(s)[-1] == "附近長者中心下晝三點有粵曲班，可以識多啲朋友。想唔想去？我幫你報名吖。", says_text(s)
    cs2 = cards(s)
    assert any(c.get("kind") == "activity" and c.get("label") == "3:00 粵曲班"
               and "長者中心" in (c.get("small") or "") and "可代報名" in (c.get("small") or "") for c in cs2), cs2
    labels2 = [st["label"] for u in steps_cmds(s) if u.get("type") == "steps" for st in u["group"]["steps"]]
    assert any("搜尋緊附近長者活動" in l for l in labels2), labels2
    assert any("檢查緊時間同交通" in l for l in labels2), labels2
    # ---- turn 3: she says yes → confirm_action → runner submits the mock form → Ah Chun notified
    await s.say("好呀")
    await s.settle()
    ok = [json.loads(r["payload"]) for r in s.db.query(
        "SELECT payload FROM events WHERE type='agent.tool_result' AND payload LIKE '%confirm_action%'")]
    assert ok and ok[-1]["result"].get("ok") is True, ok
    for _ in range(60):
        rows = s.db.query("SELECT state FROM actions")
        if rows and rows[-1]["state"] in ("done", "failed", "needs_human"):
            break
        await s.wait(1.0)
    state = s.db.one("SELECT state FROM actions")["state"]
    assert state == "done", state
    sts = [json.loads(r["payload"]) for r in s.db.query(
        "SELECT payload FROM events WHERE type='action.status' ORDER BY rowid")]
    assert sts and sts[-1]["status"] == "done", sts
    assert sts[-1]["detail"].get("reference"), sts           # booking reference extracted
    ob = s.outbox()
    assert any(r["channel"] == "telegram" and r["kind"] == "NOTIFY" and r["to_contact"] == "c_chun" for r in ob), ob
    assert any("報" in t for t in says_text(s)), says_text(s)     # she is told the result
