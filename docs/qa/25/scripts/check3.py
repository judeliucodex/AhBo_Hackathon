"""QA 25 check 3a: S3, then she types 「好呀，幫我報名啦」 in the page → Action Runner (Playwright on the server's own
/mock-centre/) → DONE with a booking reference, screenshots, 阿珍 notified, 「已報名」 card.

Usage: python /scratch/check3.py [tag]        (tag names the evidence files; default "done")
"""
import json
import os
import sys

sys.path.insert(0, "/scratch")
from playwright.sync_api import sync_playwright

import common as c

TAG = sys.argv[1] if len(sys.argv) > 1 else "done"
OUT = os.path.join(c.OUT, "check3")
os.makedirs(OUT, exist_ok=True)


def lines(page, w, skip=("ack", "heartbeat")):
    fr = page.evaluate(f"window.__wsframes.slice({w})")
    base = fr[0]["t"] if fr else 0
    out = []
    for f in fr:
        o = f.get("obj") or {}
        if o.get("type") in skip:
            continue
        if o.get("type") == "ui" and (o.get("ui") or {}).get("type") in ("level",):
            continue
        out.append(f"  +{f['t'] - base:6d} {f['dir']:3s} " + json.dumps({k: v for k, v in o.items() if k != "id"}, ensure_ascii=False)[:230])
    return out


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

    t0 = page.evaluate("window.__simtrace.length")
    print("scenario S3:", c.api(ctx, "POST", "/operator/api/scenario/S3"))
    # turn 2's reply is the second say_end
    i = c.wait_face(page, "cmd => cmd.type === 'say_end'", since=t0, timeout=60)
    c.wait_face(page, "cmd => cmd.type === 'say_end'", since=i, timeout=60)
    page.wait_for_timeout(1200)
    print("after S3 turn 2 — session:", page.inner_text("#sess"), "| text box enabled:",
          page.evaluate("document.getElementById('sayText').disabled === false"))
    page.locator("#bezel").screenshot(path=os.path.join(OUT, f"{TAG}_0_after_s3_turn2.png"))
    if page.evaluate("document.getElementById('sayText').disabled"):
        print("text box is off → tapping the face to wake first")
        page.frame_locator("#face").locator("#stage").click(position={"x": 590, "y": 300})
        page.wait_for_function("document.getElementById('sayText').disabled === false", timeout=8000)
        print("session after wake:", page.inner_text("#sess"))

    w1 = c.mark(page)
    t1 = page.evaluate("window.__simtrace.length")
    page.fill("#sayText", "好呀，幫我報名啦")
    page.click("#sayBtn")
    print("typed 「好呀，幫我報名啦」")
    try:
        c.wait_in(page, "f => f.type === 'ui' && f.ui.type === 'card' && String(f.ui.card.id).startsWith('action_')", since=w1, timeout=150, label="action card")
        print("→ action card arrived")
    except TimeoutError as e:
        print("!! no action card:", e)
    page.wait_for_timeout(900)
    page.locator("#bezel").screenshot(path=os.path.join(OUT, f"{TAG}_1_action_card.png"))
    dom = c.face_dom(page)
    print("face: cards", [(x["id"][:14], x["text"]) for x in dom["cards"]], "| steps", [s["text"] for s in dom["steps"]], "| caption", dom["caption"][:60])
    page.wait_for_timeout(9000)
    page.locator("#bezel").screenshot(path=os.path.join(OUT, f"{TAG}_2_after_result_line.png"))
    page.screenshot(path=os.path.join(OUT, f"{TAG}_2_fullpage.png"))
    print("\n".join(lines(page, w1)))
    tr = page.evaluate(f"window.__simtrace.slice({t1})")
    print("spoken lines after her yes:", [e["obj"].get("text") for e in tr if e["dir"] == "face" and e["obj"].get("type") == "say_start"])
    json.dump(page.evaluate(f"window.__wsframes.slice({w1})"), open(os.path.join(OUT, f"{TAG}.ws.json"), "w"), ensure_ascii=False, indent=1)

    ob = c.api(ctx, "GET", "/operator/api/outbox?limit=50")[1]
    rows = ob.get("items", []) if isinstance(ob, dict) else []
    json.dump(rows, open(os.path.join(OUT, f"{TAG}.outbox.json"), "w"), ensure_ascii=False, indent=1)
    print("outbox:")
    for r in rows:
        pl = r.get("payload") or {}
        print("   ", r.get("channel"), "|", r.get("kind"), "| to", r.get("to_contact"), "|", json.dumps(pl.get("text"), ensure_ascii=False)[:230],
              "| screenshots:", pl.get("screenshots"), "| buttons", [b.get("text") if isinstance(b, dict) else b for b in (r.get("buttons") or [])])
    st = c.api(ctx, "GET", "/operator/api/state")[1] or {}
    print("state keys:", sorted(st.keys()))
    print("actions in state:", json.dumps(st.get("actions") or st.get("action"), ensure_ascii=False)[:600])
    browser.close()
