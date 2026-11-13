"""First probe: login, open /sim/, see the idle face and the handshake frames."""
import json
import sys

sys.path.insert(0, "/scratch")
from playwright.sync_api import sync_playwright

import common as c

with sync_playwright() as pw:
    b = pw.chromium.launch()
    ctx = c.new_context(b)
    print("cookies:", c.login(ctx))
    print("cookie flags:", [{k: x[k] for k in ("name", "httpOnly", "secure", "sameSite")} for x in ctx.cookies()])
    print("state:", c.api(ctx, "GET", "/operator/api/state")[0])
    print("reset:", c.api(ctx, "POST", "/operator/api/reset"))
    logs = []
    page = ctx.new_page()
    page.on("console", lambda m: logs.append(f"{m.type}: {m.text[:200]}"))
    page.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))
    page.on("requestfailed", lambda r: logs.append(f"REQFAILED: {r.url} {r.failure}"))
    page.goto(c.BASE + "/sim/", wait_until="load")
    page.wait_for_function("document.getElementById('conn').dataset.state === 'online'", timeout=20000)
    page.wait_for_timeout(2500)
    fr = c.wsframes(page)
    print("ws frames:", len(fr))
    for f in fr[:25]:
        o = f.get("obj") or {}
        print(" ", f["dir"], json.dumps(o, ensure_ascii=False)[:260] if o else {k: v for k, v in f.items() if k not in ("obj",)})
    print("dom:", json.dumps(c.face_dom(page), ensure_ascii=False)[:900])
    print("panel:", page.evaluate("({conn: document.getElementById('conn').textContent, dev: document.getElementById('devId').textContent, clips: document.getElementById('clipVer').textContent, sess: document.getElementById('sess').textContent, sayDisabled: document.getElementById('sayText').disabled, hint: document.getElementById('sayHint').textContent})"))
    c.shot(page, "probe_idle")
    c.face_shot(page, "probe_idle_face")
    print("bezel box:", page.locator("#bezel").bounding_box())
    print("console/log lines:")
    for line in logs[:30]:
        print("  ", line)
    b.close()
