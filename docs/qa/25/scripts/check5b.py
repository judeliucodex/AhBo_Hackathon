"""QA 25 check 5b: real network cut. The host disconnects THIS runner container from the docker network for 35 s
(after /scratch/out/offline_go appears), then reconnects it. The page samples the offline dot throughout."""
import json
import os
import sys
import time

sys.path.insert(0, "/scratch")
from playwright.sync_api import sync_playwright

import common as c

OUT = os.path.join(c.OUT, "check45")
os.makedirs(OUT, exist_ok=True)
GO = os.path.join(c.OUT, "offline_go")

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    ctx = c.new_context(browser)
    c.login(ctx)
    page = ctx.new_page()
    page.goto(c.BASE + "/sim/", wait_until="load")
    page.wait_for_function("document.getElementById('conn').dataset.state === 'online'", timeout=20000)
    page.frame_locator("#face").locator("#eyes").wait_for()
    c.api(ctx, "POST", "/operator/api/reset")
    page.wait_for_timeout(1500)
    w, t = c.mark(page), page.evaluate("window.__simtrace.length")
    open(GO, "w").write("go")
    t0 = time.time()
    shots = set()
    last = None
    while time.time() - t0 < 80:
        el = time.time() - t0
        d = c.face_dom(page)
        cur = (page.inner_text("#conn"), d["offline"]["cls"], d["offline"]["visible"])
        if cur != last:
            print(f"  t+{el:5.1f}s conn pill {cur[0]!r} | offline dot class {cur[1]!r} visible {cur[2]}")
            last = cur
        for mark in (25, 34, 50, 75):
            if el >= mark and mark not in shots:
                shots.add(mark)
                page.locator("#bezel").screenshot(path=os.path.join(OUT, f"5b_real_t{mark}.png"))
        page.wait_for_timeout(500)
    tr = page.evaluate(f"window.__simtrace.slice({t})")
    print("face offline cmds:", [(round((e["t"]) / 1000, 1), e["obj"]) for e in tr if e["dir"] == "face" and e["obj"].get("type") == "offline"])
    fr = page.evaluate(f"window.__wsframes.slice({w})")
    b = fr[0]["t"] if fr else 0
    print("ws open/close/hello/welcome:", [(round((f["t"] - b) / 1000, 1), f["dir"], f.get("code") or (f.get("obj") or {}).get("type")) for f in fr
                                           if f["dir"] in ("open", "close") or (f.get("obj") or {}).get("type") in ("hello", "welcome")])
    print("replay after reconnect:", [json.dumps(f["obj"].get("ui"), ensure_ascii=False)[:60] for f in fr if f["dir"] == "in" and (f.get("obj") or {}).get("type") == "ui"][-6:])
    json.dump(fr, open(os.path.join(OUT, "5b_real_offline.ws.json"), "w"), ensure_ascii=False, indent=1)
    ts = page.evaluate("window.__simtrace.length")
    print("S2 after reconnect:", c.api(ctx, "POST", "/operator/api/scenario/S2")[0])
    try:
        c.wait_face(page, "cmd => cmd.type === 'say_end'", since=ts, timeout=40)
        print("  reply played ✓")
    except TimeoutError as e:
        print("  !!", e)
    os.remove(GO)
    browser.close()
