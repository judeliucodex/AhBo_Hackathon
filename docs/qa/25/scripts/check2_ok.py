"""QA 25 check 2, step 3 diagnostics: fall → press the face's on-screen 「我冇事」 → record everything for 20 s.

Usage: python /scratch/check2_ok.py [--panel]   (--panel presses the tester panel's 我冇事 button instead)
       python /scratch/check2_ok.py --fresh       (no wake/medicine turn before the fall)
"""
import json
import os
import sys

sys.path.insert(0, "/scratch")
from playwright.sync_api import sync_playwright

import common as c

PANEL = "--panel" in sys.argv
FRESH = "--fresh" in sys.argv
TAG = ("panel" if PANEL else "face") + ("_fresh" if FRESH else "_afterturn")
OUT = os.path.join(c.OUT, "check2")
os.makedirs(OUT, exist_ok=True)

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
    page.wait_for_timeout(1200)
    if not FRESH:
        page.frame_locator("#face").locator("#stage").click(position={"x": 590, "y": 300})
        page.wait_for_function("document.getElementById('sayText').disabled === false", timeout=8000)
        t0 = page.evaluate("window.__simtrace.length")
        page.fill("#sayText", "阿寶，我幾時食藥呀？")
        page.click("#sayBtn")
        c.wait_face(page, "cmd => cmd.type === 'say_end'", since=t0, timeout=40)
        page.wait_for_timeout(500)
        print("turn before the fall done")

    t0 = page.evaluate("window.__simtrace.length")
    w0 = c.mark(page)
    print("scenario:", c.api(ctx, "POST", "/operator/api/scenario/S4"))
    c.wait_face(page, "cmd => cmd.type === 'ok_button' && cmd.show", since=t0, timeout=60)
    page.wait_for_timeout(700)
    print("state before press:", json.dumps((c.api(ctx, "GET", "/operator/api/state")[1] or {}).get("incident"), ensure_ascii=False)[:300])
    w1 = c.mark(page)
    if PANEL:
        page.click("#okBtn")
    else:
        page.frame_locator("#face").locator("#okbtn").click()
    print("pressed", "panel #okBtn" if PANEL else "face #okbtn")
    page.wait_for_timeout(1500)
    page.locator("#bezel").screenshot(path=os.path.join(OUT, f"3_{TAG}_1500ms_after_press.png"))
    page.wait_for_timeout(18500)
    page.locator("#bezel").screenshot(path=os.path.join(OUT, f"3_{TAG}_20s_after_press.png"))
    fr = page.evaluate(f"window.__wsframes.slice({w1})")
    base = fr[0]["t"] if fr else 0
    print("frames after the press:")
    for f in fr:
        o = f.get("obj") or {}
        if o.get("type") in ("ack", "heartbeat"):
            continue
        print(f"  +{f['t'] - base:6d} {f['dir']:3s}", json.dumps({k: v for k, v in o.items() if k != "id"}, ensure_ascii=False)[:210])
    st = c.api(ctx, "GET", "/operator/api/state")[1] or {}
    print("state 20 s after press:", json.dumps(st.get("incident"), ensure_ascii=False)[:400])
    dom = c.face_dom(page)
    print("face: mood", dom["mood"], "| steps", [s["text"] for s in dom["steps"]], "| cards", [x["text"] for x in dom["cards"]], "| ok visible", dom["ok"]["visible"])
    json.dump(page.evaluate(f"window.__wsframes.slice({w0})"), open(os.path.join(OUT, f"3_{TAG}.ws.json"), "w"), ensure_ascii=False, indent=1)
    ob = c.api(ctx, "GET", "/operator/api/outbox?limit=50")[1]
    rows = ob.get("items", []) if isinstance(ob, dict) else []
    json.dump(rows, open(os.path.join(OUT, f"3_{TAG}.outbox.json"), "w"), ensure_ascii=False, indent=1)
    print("outbox:")
    for r in rows:
        pl = r.get("payload") or {}
        print("  ", r.get("channel"), r.get("kind"), r.get("to_contact"), json.dumps(pl.get("text"), ensure_ascii=False)[:100], "buttons", json.dumps(r.get("buttons"), ensure_ascii=False)[:80])
    browser.close()
