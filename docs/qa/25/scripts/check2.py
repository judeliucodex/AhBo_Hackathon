"""QA 25 check 2: her side of the page — tap-to-wake, medicine question in the text box, the face's on-screen
「我冇事」 during a fall, and typing 「救命」 during the countdown. Also dumps the outbox for both branches.

Usage: python /scratch/check2.py
"""
import json
import os
import sys

sys.path.insert(0, "/scratch")
from playwright.sync_api import sync_playwright

import common as c

OUT = os.path.join(c.OUT, "check2")
os.makedirs(OUT, exist_ok=True)


def dump_outbox(ctx, name):
    st, ob = c.api(ctx, "GET", "/operator/api/outbox?limit=200")
    rows = ob.get("items", []) if isinstance(ob, dict) else []
    json.dump(rows, open(os.path.join(OUT, f"{name}.outbox.json"), "w"), ensure_ascii=False, indent=1)
    print(f"outbox[{name}] ({len(rows)} rows, newest first):")
    for r in rows:
        pl = r.get("payload") or {}
        print("  ", r.get("ts"), "|", r.get("channel"), "|", r.get("kind"), "| to:", r.get("to_contact"),
              "| text:", json.dumps(pl.get("text") or r.get("text"), ensure_ascii=False)[:110],
              "| buttons:", json.dumps(r.get("buttons"), ensure_ascii=False)[:90],
              "| keys:", sorted(pl.keys()))
    return rows


def simtrace_len(page):
    return page.evaluate("window.__simtrace.length")


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    ctx = c.new_context(browser)
    c.login(ctx)
    page = ctx.new_page()
    page.on("pageerror", lambda e: print("PAGEERROR:", e))
    page.goto(c.BASE + "/sim/", wait_until="load")
    page.wait_for_function("document.getElementById('conn').dataset.state === 'online'", timeout=20000)
    page.frame_locator("#face").locator("#eyes").wait_for()
    page.click("#sound")
    print("reset:", c.api(ctx, "POST", "/operator/api/reset"))
    page.wait_for_timeout(1500)

    # ---- 1. idle: text box disabled; tap the face -> wake + text session ----
    print("\n== 1. wake by tapping the face while idle ==")
    print("text box disabled while idle:", page.evaluate("document.getElementById('sayText').disabled"),
          "| hint:", page.inner_text("#sayHint"))
    t0 = simtrace_len(page)
    w0 = c.mark(page)
    page.frame_locator("#face").locator("#stage").click(position={"x": 590, "y": 300})
    page.wait_for_timeout(350)
    tr = page.evaluate(f"window.__simtrace.slice({t0})")
    print("first trace entries after the tap:", [(e["dir"], (e["obj"].get("type"), e["obj"].get("expr") or e["obj"].get("trigger") or e["obj"].get("mood") or "")) for e in tr[:8]])
    page.wait_for_function("document.getElementById('sayText').disabled === false", timeout=8000)
    fr = page.evaluate(f"window.__wsframes.slice({w0})")
    for f in fr:
        o = f.get("obj") or {}
        if o.get("type") in ("wake", "session.open", "ui", "wake.enable"):
            print("  ", f["dir"], json.dumps({k: v for k, v in o.items() if k != "id"}, ensure_ascii=False)[:200])
    print("after wake — session:", page.inner_text("#sess"), "| hint:", page.inner_text("#sayHint"))
    print("face:", json.dumps({k: c.face_dom(page)[k] for k in ("mood",)}, ensure_ascii=False))
    page.screenshot(path=os.path.join(OUT, "1_wake_fullpage.png"))

    # ---- 2. medicine question through the text box ----
    print("\n== 2. 「阿寶，我幾時食藥呀？」 via the text box ==")
    t0 = simtrace_len(page)
    page.fill("#sayText", "阿寶，我幾時食藥呀？")
    page.click("#sayBtn")
    c.wait_face(page, "cmd => cmd.type === 'say_end'", since=t0, timeout=40)
    page.wait_for_timeout(500)
    dom = c.face_dom(page)
    tr = page.evaluate(f"window.__simtrace.slice({t0})")
    says = [e["obj"] for e in tr if e["dir"] == "face" and e["obj"].get("type") == "say_start"]
    print("reply text:", says[-1].get("text") if says else None)
    print("cards:", [(x["id"], x["text"]) for x in dom["cards"]])
    print("steps:", [s["text"] for s in dom["steps"]])
    print("heard:", dom["heard"])
    print("face cmd kinds in order:", [e["obj"]["type"] + (":" + str(e["obj"].get("mood") or e["obj"].get("state") or "") if e["obj"]["type"] in ("mood", "step") else "") for e in tr if e["dir"] == "face" and e["obj"]["type"] != "level"])
    page.locator("#bezel").screenshot(path=os.path.join(OUT, "2_med_reply.png"))
    page.screenshot(path=os.path.join(OUT, "2_med_reply_fullpage.png"))

    # ---- 3. fall + press the on-screen 我冇事 ----
    print("\n== 3. S4, then press the face's on-screen 「我冇事」 ==")
    t0 = simtrace_len(page)
    w0 = c.mark(page)
    print("scenario:", c.api(ctx, "POST", "/operator/api/scenario/S4"))
    c.wait_face(page, "cmd => cmd.type === 'ok_button' && cmd.show", since=t0, timeout=60)
    page.wait_for_timeout(700)
    print("countdown chip at press time:", page.frame_locator("#face").locator(".chip.count").inner_text().strip())
    print("OK button box:", page.frame_locator("#face").locator("#okbtn").bounding_box())
    page.locator("#bezel").screenshot(path=os.path.join(OUT, "3a_before_ok_press.png"))
    t1 = simtrace_len(page)
    page.frame_locator("#face").locator("#okbtn").click()
    c.wait_in(page, "f => f.type === 'play_fixed' && f.clip_id === 'F5'", since=w0, timeout=15, label="F5")
    print("→ F5 commanded after the OK press ✓")
    page.wait_for_timeout(1200)
    page.locator("#bezel").screenshot(path=os.path.join(OUT, "3b_ok_pressed_f5.png"))
    c.wait_face(page, "cmd => cmd.type === 'steps_clear'", since=t1, timeout=30)
    page.wait_for_timeout(600)
    tr = page.evaluate(f"window.__simtrace.slice({t1})")
    print("face cmds after the press:", [json.dumps({k: v for k, v in e["obj"].items() if k not in ("segments",)}, ensure_ascii=False)[:90] for e in tr if e["dir"] == "face" and e["obj"]["type"] != "level"])
    dom = c.face_dom(page)
    print("after resolution — mood class:", dom["mood"], "| steps:", [s["text"] for s in dom["steps"]], "| cards:", [(x["id"], x["text"]) for x in dom["cards"]], "| ok visible:", dom["ok"]["visible"])
    page.locator("#bezel").screenshot(path=os.path.join(OUT, "3c_ok_resolved_end.png"))
    fr = page.evaluate(f"window.__wsframes.slice({w0})")
    print("touch_ok events sent:", len([f for f in fr if f["dir"] == "out" and (f.get("obj") or {}).get("type") == "touch_ok"]))
    json.dump(fr, open(os.path.join(OUT, "ok_branch.ws.json"), "w"), ensure_ascii=False, indent=1)
    page.wait_for_timeout(7000)   # the family INFO waits ≤ 6 s for the camera decision (BUILD_LOG §8)
    dump_outbox(ctx, "ok_branch")
    print("incident state:", json.dumps((c.api(ctx, "GET", "/operator/api/state")[1] or {}).get("incident"), ensure_ascii=False)[:300])

    # ---- 4. fall + type 救命 during the countdown ----
    print("\n== 4. S4, then type 「救命」 during the countdown ==")
    t0 = simtrace_len(page)
    w0 = c.mark(page)
    print("scenario:", c.api(ctx, "POST", "/operator/api/scenario/S4"))
    c.wait_face(page, "cmd => cmd.type === 'countdown'", since=t0, timeout=60)
    page.wait_for_timeout(600)
    print("text box enabled during the fall check:", page.evaluate("document.getElementById('sayText').disabled === false"),
          "| session:", page.inner_text("#sess"))
    t1 = simtrace_len(page)
    page.fill("#sayText", "救命")
    page.click("#sayBtn")
    c.wait_in(page, "f => f.type === 'play_fixed' && f.clip_id === 'F4'", since=w0, timeout=20, label="F4")
    print("→ escalated: F4 commanded ✓")
    page.wait_for_timeout(1000)
    dom = c.face_dom(page)
    print("heard line right after she typed 救命:", repr(dom["heard"]))
    print("steps:", [s["text"] for s in dom["steps"]], "| cards:", [(x["id"], x["text"]) for x in dom["cards"]], "| ok visible:", dom["ok"]["visible"])
    page.locator("#bezel").screenshot(path=os.path.join(OUT, "4a_help_escalated.png"))
    c.wait_face(page, "cmd => cmd.type === 'say_end'", since=t1, timeout=30)
    page.wait_for_timeout(700)
    page.locator("#bezel").screenshot(path=os.path.join(OUT, "4b_help_f4_end.png"))
    tr = page.evaluate(f"window.__simtrace.slice({t1})")
    print("face cmds after 救命:", [json.dumps({k: v for k, v in e["obj"].items() if k not in ("segments",)}, ensure_ascii=False)[:100] for e in tr if e["dir"] == "face" and e["obj"]["type"] != "level"])
    heard_cmds = [e["obj"] for e in tr if e["dir"] == "face" and e["obj"]["type"] == "heard"]
    print("heard commands sent to the face after 救命:", heard_cmds)
    fr = page.evaluate(f"window.__wsframes.slice({w0})")
    json.dump(fr, open(os.path.join(OUT, "help_branch.ws.json"), "w"), ensure_ascii=False, indent=1)
    page.wait_for_timeout(8000)
    rows = dump_outbox(ctx, "help_branch")
    print("outbox rows mentioning 999/112/911:", len([r for r in rows if any(n in json.dumps(r, ensure_ascii=False) for n in ("999", "112", "911"))]))
    print("incident state:", json.dumps((c.api(ctx, "GET", "/operator/api/state")[1] or {}).get("incident"), ensure_ascii=False)[:400])
    page.screenshot(path=os.path.join(OUT, "4c_help_end_fullpage.png"))
    open(os.path.join(OUT, "simlog.txt"), "w").write(page.inner_text("#log"))
    browser.close()
