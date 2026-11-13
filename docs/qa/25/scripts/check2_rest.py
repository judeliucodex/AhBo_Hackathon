"""QA 25 check 2, steps 3–4:
  A. S4ok (fake camera: she is fine) + press the face's on-screen 「我冇事」 before the scripted utterance → RESOLVED_OK.
  B. S4 + an irrelevant line during the countdown (must not resolve; Flash must not speak), then 「救命」 → ESCALATING.
Usage: python /scratch/check2_rest.py [A] [B]
"""
import json
import os
import sys

sys.path.insert(0, "/scratch")
from playwright.sync_api import sync_playwright

import common as c

WHICH = [a for a in sys.argv[1:] if a in ("A", "B")] or ["A", "B"]
OUT = os.path.join(c.OUT, "check2")
os.makedirs(OUT, exist_ok=True)


def frames_since(page, w, skip=("ack", "heartbeat")):
    fr = page.evaluate(f"window.__wsframes.slice({w})")
    base = fr[0]["t"] if fr else 0
    out = []
    for f in fr:
        o = f.get("obj") or {}
        if o.get("type") in skip:
            continue
        out.append(f"  +{f['t'] - base:6d} {f['dir']:3s} " + json.dumps({k: v for k, v in o.items() if k != "id"}, ensure_ascii=False)[:215])
    return out


def outbox(ctx, name):
    ob = c.api(ctx, "GET", "/operator/api/outbox?limit=50")[1]
    rows = ob.get("items", []) if isinstance(ob, dict) else []
    json.dump(rows, open(os.path.join(OUT, f"{name}.outbox.json"), "w"), ensure_ascii=False, indent=1)
    print(f"outbox [{name}] {len(rows)} rows:")
    for r in rows:
        pl = r.get("payload") or {}
        print("   ", r.get("channel"), "|", r.get("kind"), "| to", r.get("to_contact"), "|", json.dumps(pl.get("text"), ensure_ascii=False)[:150],
              "| buttons", [b.get("text") if isinstance(b, dict) else b for b in (r.get("buttons") or [])],
              "| photo keys", [k for k in pl if "photo" in k or "screenshot" in k], [pl.get(k) for k in pl if "photo" in k][:1])
    return rows


def incident(ctx):
    st = c.api(ctx, "GET", "/operator/api/state")[1] or {}
    inc = st.get("incident") or {}
    return {k: inc.get(k) for k in ("state", "kind", "source", "why", "acked_by", "ok_how", "calls_placed", "vision_note")}, \
        [(t.get("state") or t.get("what"), t.get("why") or "") for t in inc.get("timeline", []) if t.get("state")]


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

    if "A" in WHICH:
        print("\n===== A. S4ok + press the face's on-screen 我冇事 (camera fine) =====")
        t0 = page.evaluate("window.__simtrace.length")
        w0 = c.mark(page)
        print("scenario:", c.api(ctx, "POST", "/operator/api/scenario/S4ok"))
        c.wait_face(page, "cmd => cmd.type === 'ok_button' && cmd.show", since=t0, timeout=60)
        w1 = c.mark(page)
        page.frame_locator("#face").locator("#okbtn").click()
        print("pressed the face #okbtn right after it appeared")
        page.wait_for_timeout(1500)
        page.locator("#bezel").screenshot(path=os.path.join(OUT, "A_ok_pressed_1500ms.png"))
        page.wait_for_timeout(9000)
        print("\n".join(frames_since(page, w1)))
        page.locator("#bezel").screenshot(path=os.path.join(OUT, "A_ok_resolved_end.png"))
        print("incident:", incident(ctx))
        dom = c.face_dom(page)
        print("face: mood", dom["mood"], "| steps", [s["text"] for s in dom["steps"]], "| cards", [x["text"] for x in dom["cards"]], "| ok visible", dom["ok"]["visible"])
        json.dump(page.evaluate(f"window.__wsframes.slice({w0})"), open(os.path.join(OUT, "A_ok_press.ws.json"), "w"), ensure_ascii=False, indent=1)
        outbox(ctx, "A_ok_press")

    if "B" in WHICH:
        print("\n===== B. S4 + irrelevant line, then 救命 during the countdown =====")
        t0 = page.evaluate("window.__simtrace.length")
        w0 = c.mark(page)
        print("scenario:", c.api(ctx, "POST", "/operator/api/scenario/S4"))
        c.wait_face(page, "cmd => cmd.type === 'countdown'", since=t0, timeout=60)
        print("text box enabled:", page.evaluate("document.getElementById('sayText').disabled === false"), "| session:", page.inner_text("#sess"))
        w1 = c.mark(page)
        page.fill("#sayText", "今日天氣點呀？")
        page.click("#sayBtn")
        page.wait_for_timeout(1500)
        print("-- 1.5 s after an irrelevant line:")
        print("\n".join(frames_since(page, w1)))
        print("incident:", incident(ctx)[0])
        dom = c.face_dom(page)
        print("heard line:", repr(dom["heard"]), "| ok visible:", dom["ok"]["visible"])
        page.locator("#bezel").screenshot(path=os.path.join(OUT, "B1_irrelevant_line.png"))
        w2 = c.mark(page)
        page.fill("#sayText", "救命")
        page.click("#sayBtn")
        c.wait_in(page, "f => f.type === 'play_fixed' && f.clip_id === 'F4'", since=w2, timeout=20, label="F4")
        page.wait_for_timeout(900)
        dom = c.face_dom(page)
        print("-- after 救命: heard line:", repr(dom["heard"]), "| steps", [s["text"] for s in dom["steps"]], "| cards", [x["text"] for x in dom["cards"]])
        page.locator("#bezel").screenshot(path=os.path.join(OUT, "B2_help_escalated.png"))
        page.wait_for_timeout(9000)
        print("\n".join(frames_since(page, w2)))
        page.locator("#bezel").screenshot(path=os.path.join(OUT, "B3_help_f4_end.png"))
        print("incident:", incident(ctx))
        json.dump(page.evaluate(f"window.__wsframes.slice({w0})"), open(os.path.join(OUT, "B_help.ws.json"), "w"), ensure_ascii=False, indent=1)
        rows = outbox(ctx, "B_help")
        blob = json.dumps(rows, ensure_ascii=False)
        print("outbox mentions 999/112/911:", [n for n in ("999", "112", "911") if n in blob])
    page.screenshot(path=os.path.join(OUT, "check2_rest_end_fullpage.png"))
    browser.close()
