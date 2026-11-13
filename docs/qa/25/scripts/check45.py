"""QA 25 checks 4 + 5: voice note trigger; reload mid-scenario; 35 s offline; two tabs.
Usage: python /scratch/check45.py [4] [reload] [offline] [tabs]"""
import json
import os
import sys

sys.path.insert(0, "/scratch")
from playwright.sync_api import sync_playwright

import common as c

WHICH = sys.argv[1:] or ["4", "reload", "offline", "tabs"]
OUT = os.path.join(c.OUT, "check45")
os.makedirs(OUT, exist_ok=True)


def lines(page, w, limit=230):
    fr = page.evaluate(f"window.__wsframes.slice({w})")
    base = fr[0]["t"] if fr else 0
    out = []
    for f in fr:
        o = f.get("obj")
        if o is None:
            out.append(f"  +{f['t'] - base:6d} {f['dir']:5s} {f.get('url') or f.get('code') or ''}")
            continue
        if o.get("type") in ("ack", "heartbeat"):
            continue
        out.append(f"  +{f['t'] - base:6d} {f['dir']:5s} " + json.dumps({k: v for k, v in o.items() if k not in ("id", "config", "device_token")}, ensure_ascii=False)[:limit])
    return out


def summary(page):
    d = c.face_dom(page)
    return {"mood": d["mood"], "caption": d["caption"][:40], "cards": [x["text"] for x in d["cards"]],
            "steps": [s["text"] for s in d["steps"]], "ok": d["ok"]["visible"], "offline_dot": d["offline"]}


def ready(page, q=""):
    page.goto(c.BASE + "/sim/" + q, wait_until="load")
    page.wait_for_function("document.getElementById('conn').dataset.state === 'online'", timeout=20000)
    page.frame_locator("#face").locator("#eyes").wait_for()
    page.wait_for_timeout(700)


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    ctx = c.new_context(browser)
    c.login(ctx)
    page = ctx.new_page()
    page.on("pageerror", lambda e: print("PAGEERROR:", e))
    ready(page)
    page.click("#sound")

    if "4" in WHICH:
        print("\n===== 4. voice note trigger =====")
        print("reset:", c.api(ctx, "POST", "/operator/api/reset")[1])
        page.wait_for_timeout(1500)
        w = c.mark(page)
        t = page.evaluate("window.__simtrace.length")
        print("trigger:", c.api(ctx, "POST", "/operator/api/trigger", {"kind": "voice_note"}))
        for i, ms in enumerate((1500, 2500, 3000, 3000, 5000)):
            page.wait_for_timeout(ms)
            page.locator("#bezel").screenshot(path=os.path.join(OUT, f"4_voice_note_{i}.png"))
            print(f"  shot {i}:", json.dumps(summary(page), ensure_ascii=False))
        print("\n".join(lines(page, w)))
        tr = page.evaluate(f"window.__simtrace.slice({t})")
        print("face say_start cmds:", [json.dumps({k: v for k, v in e["obj"].items() if k != "segments"}, ensure_ascii=False) for e in tr if e["dir"] == "face" and e["obj"].get("type") == "say_start"])
        print("-- she answers 好呀 (text box enabled:", page.evaluate("document.getElementById('sayText').disabled === false"), ")")
        w2 = c.mark(page)
        page.fill("#sayText", "好呀")
        page.click("#sayBtn")
        for i, ms in enumerate((2500, 2500, 4000)):
            page.wait_for_timeout(ms)
            page.locator("#bezel").screenshot(path=os.path.join(OUT, f"4_voice_note_yes_{i}.png"))
            d = c.face_dom(page)
            print(f"  shot yes_{i}:", json.dumps(summary(page), ensure_ascii=False), "| card classes:", [x["cls"] for x in d["cards"]])
        print("\n".join(lines(page, w2, 330)))
        json.dump(page.evaluate(f"window.__wsframes.slice({w})"), open(os.path.join(OUT, "4_voice_note.ws.json"), "w"), ensure_ascii=False, indent=1)

    if "reload" in WHICH:
        print("\n===== 5a. reload the page mid-scenario (S4, during the countdown) =====")
        t = page.evaluate("window.__simtrace.length")
        print("scenario:", c.api(ctx, "POST", "/operator/api/scenario/S4"))
        c.wait_face(page, "cmd => cmd.type === 'countdown'", since=t, timeout=60)
        page.wait_for_timeout(800)
        print("before reload:", json.dumps(summary(page), ensure_ascii=False))
        page.locator("#bezel").screenshot(path=os.path.join(OUT, "5a_before_reload.png"))
        page.reload(wait_until="load")
        page.wait_for_function("document.getElementById('conn').dataset.state === 'online'", timeout=20000)
        page.wait_for_timeout(1200)
        print("1.2 s after reload:", json.dumps(summary(page), ensure_ascii=False))
        page.locator("#bezel").screenshot(path=os.path.join(OUT, "5a_after_reload.png"))
        print("\n".join(lines(page, 0)[:30]))
        page.wait_for_timeout(11000)
        print("12 s after reload:", json.dumps(summary(page), ensure_ascii=False))
        page.locator("#bezel").screenshot(path=os.path.join(OUT, "5a_after_reload_12s.png"))
        print("\n".join(lines(page, 0)[30:60]))
        print("incident:", ((c.api(ctx, "GET", "/operator/api/state")[1] or {}).get("incident") or {}).get("state"))
        json.dump(page.evaluate("window.__wsframes"), open(os.path.join(OUT, "5a_reload.ws.json"), "w"), ensure_ascii=False, indent=1)
        print("\n-- 5a2. 'Reload face' button mid S2 reply --")
        t = page.evaluate("window.__simtrace.length")
        c.api(ctx, "POST", "/operator/api/scenario/S2")
        c.wait_face(page, "cmd => cmd.type === 'say_start'", since=t, timeout=40)
        page.wait_for_timeout(1500)
        w = c.mark(page)
        page.click("#reloadFace")
        page.wait_for_timeout(2500)
        print("2.5 s after Reload face:", json.dumps(summary(page), ensure_ascii=False))
        page.locator("#bezel").screenshot(path=os.path.join(OUT, "5a2_after_reload_face.png"))
        print("\n".join(lines(page, w)[:14]))
        page.wait_for_timeout(9000)

    if "offline" in WHICH:
        print("\n===== 5b. network off for 35 s =====")
        print("reset:", c.api(ctx, "POST", "/operator/api/reset")[1])
        page.wait_for_timeout(1500)
        w = c.mark(page)
        t = page.evaluate("window.__simtrace.length")
        cdp = ctx.new_cdp_session(page)
        cdp.send("Network.enable")
        cdp.send("Network.emulateNetworkConditions", {"offline": True, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1})
        for s in (5, 20, 28, 32, 35):
            page.wait_for_timeout((s - (0 if s == 5 else {20: 5, 28: 20, 32: 28, 35: 32}[s])) * 1000)
            d = c.face_dom(page)
            print(f"  t+{s}s conn pill: {page.inner_text('#conn')!r} | offline dot: {d['offline']}")
            if s in (28, 32, 35):
                page.locator("#bezel").screenshot(path=os.path.join(OUT, f"5b_offline_t{s}.png"))
        cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1})
        for s in (2, 6, 12, 20):
            page.wait_for_timeout({2: 2, 6: 4, 12: 6, 20: 8}[s] * 1000)
            d = c.face_dom(page)
            print(f"  back online +{s}s conn pill: {page.inner_text('#conn')!r} | offline dot: {d['offline']}")
        page.locator("#bezel").screenshot(path=os.path.join(OUT, "5b_back_online.png"))
        tr = page.evaluate(f"window.__simtrace.slice({t})")
        print("face offline cmds:", [(e["t"], e["obj"]) for e in tr if e["dir"] == "face" and e["obj"].get("type") == "offline"])
        fr = page.evaluate(f"window.__wsframes.slice({w})")
        print("ws opens/closes:", [(f["dir"], f.get("code")) for f in fr if f["dir"] in ("open", "close")])
        # does it still work after coming back?
        t = page.evaluate("window.__simtrace.length")
        c.api(ctx, "POST", "/operator/api/scenario/S2")
        try:
            c.wait_face(page, "cmd => cmd.type === 'say_end'", since=t, timeout=40)
            print("S2 after reconnect: reply played ✓", summary(page)["caption"])
        except TimeoutError as e:
            print("!! S2 after reconnect:", e)
        json.dump(fr, open(os.path.join(OUT, "5b_offline.ws.json"), "w"), ensure_ascii=False, indent=1)
        page.wait_for_timeout(6000)

    if "tabs" in WHICH:
        print("\n===== 5c. two /sim/ tabs at once =====")
        page2 = ctx.new_page()
        page2.on("pageerror", lambda e: print("PAGEERROR tab2:", e))
        ready(page2)
        page.wait_for_timeout(1500)
        print("tab1:", page.inner_text("#conn"), page.inner_text("#devId"), "| tab2:", page2.inner_text("#conn"), page2.inner_text("#devId"))
        st = c.api(ctx, "GET", "/operator/api/state")[1] or {}
        print("server devices:", json.dumps(st.get("devices"), ensure_ascii=False)[:400])
        for name in ("S2", "S4ok"):
            a, b = c.mark(page), c.mark(page2)
            ta, tb = page.evaluate("window.__simtrace.length"), page2.evaluate("window.__simtrace.length")
            c.api(ctx, "POST", f"/operator/api/scenario/{name}")
            need = 1 if name == "S2" else 4
            page.wait_for_timeout(16000 if name == "S2" else 24000)
            for label, pg, m, tt in (("tab1", page, a, ta), ("tab2", pg2 := page2, b, tb)):
                fr = pg.evaluate(f"window.__wsframes.slice({m})")
                cmds = [f["obj"] for f in fr if f["dir"] == "in" and f.get("obj") and f["obj"].get("type") not in ("welcome",)]
                outs = [f["obj"]["type"] for f in fr if f["dir"] == "out" and f.get("obj") and f["obj"].get("type") not in ("ack", "heartbeat")]
                says = [e["obj"].get("text") for e in pg.evaluate(f"window.__simtrace.slice({tt})") if e["dir"] == "face" and e["obj"].get("type") == "say_start"]
                print(f"  {name} {label}: {len(cmds)} commands in, events out {outs}, captions {says}, conn {pg.inner_text('#conn')}, closes {[f.get('code') for f in fr if f['dir'] == 'close']}")
                pg.locator("#bezel").screenshot(path=os.path.join(OUT, f"5c_{name}_{label}.png"))
            inc = (c.api(ctx, "GET", "/operator/api/state")[1] or {}).get("incident") or {}
            print(f"  {name} incident state:", inc.get("state"), [(x.get("state"), x.get("why")) for x in inc.get("timeline", []) if x.get("state")])
        ob = c.api(ctx, "GET", "/operator/api/outbox?limit=20")[1]
        print("  outbox:", [(r.get("channel"), r.get("kind")) for r in ob.get("items", [])])
    browser.close()
