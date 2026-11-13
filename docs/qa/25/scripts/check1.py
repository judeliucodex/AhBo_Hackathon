"""QA 25 check 1: run S1, S2, S3, S4, S4ok on /sim/ through the scenario API; keyframe screenshots with the
same rules as evals/visual/kit_capture.py (so they line up with docs/screens/kit/), plus the face DOM at each
keyframe, the face command trace, and every WebSocket frame the page saw.

Usage: python /scratch/check1.py [S1 S2 ...] [--nosound] [--sub NAME]
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, "/scratch")
from playwright.async_api import async_playwright

import common as c

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
STORIES = ARGS or ["S1", "S2", "S3", "S4", "S4ok"]
NOSOUND = "--nosound" in sys.argv
SUB = "check1"
if "--sub" in sys.argv:
    SUB = sys.argv[sys.argv.index("--sub") + 1]
OUT = os.path.join(c.OUT, SUB)
os.makedirs(OUT, exist_ok=True)
NEED_SAY_END = {"S1": 2, "S2": 1, "S3": 2, "S4": 4, "S4ok": 4}

RECORDER = """
(() => {
  if (window.__rec) return;
  window.__rec = [];
  addEventListener("message", ev => {
    if (ev.data && ev.data.ahbo) {
      const cmd = ev.data.ahbo;
      window.__rec.push({t: performance.now(), cmd});
      if (window.parent && window.parent.__keyframe) window.parent.__keyframe(cmd);
    }
  });
})();
"""

DOM_JS = """() => {
  const q = s => Array.from(document.querySelectorAll(s));
  const vis = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
                      return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && Number(cs.opacity) > 0.01; };
  const ok = document.getElementById('okbtn'); const okr = ok.getBoundingClientRect();
  const off = document.getElementById('offline');
  return {
    faceClass: document.getElementById('face').className,
    bodyClass: document.body.className,
    stageClass: document.getElementById('stage').className,
    heard: document.getElementById('heard').textContent,
    caption: document.getElementById('caption').textContent,
    captionHtml: document.getElementById('caption').innerHTML.slice(0, 1500),
    cards: q('#cards .chip').map(ch => ({ id: ch.dataset.id, cls: ch.className, text: ch.innerText.replace(/\\s+/g, ' ').trim(),
                                          html: ch.innerHTML.slice(0, 400) })),
    stepsHtml: document.getElementById('steps').innerHTML.slice(0, 3000),
    stepsText: document.getElementById('steps').innerText,
    ok: { visible: vis(ok), cls: ok.className, text: ok.textContent.trim(), w: Math.round(okr.width), h: Math.round(okr.height) },
    offline: { cls: off.className, visible: vis(off) },
    vw: innerWidth, vh: innerHeight,
  };
}"""


def keyframe_name(cmd):
    t = cmd.get("type")
    if t == "say_end":
        return f"say_end_{cmd.get('id')}"
    if t == "countdown":
        return "countdown"
    if t == "ok_button" and cmd.get("show"):
        return "ok_button"
    if t == "mood" and cmd.get("mood") in ("alarm", "warm", "curious", "thinking"):
        return f"mood_{cmd['mood']}"
    if t == "step" and cmd.get("state") in ("done", "failed"):
        return f"step_{cmd.get('group')}_{cmd.get('id')}_{cmd['state']}"
    return None


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 1600, "height": 1000})
        await ctx.add_init_script(c.WS_HOOK)
        r = await ctx.request.post(c.BASE + "/login", form={"password": c.PASSWORD}, max_redirects=0)
        assert r.status in (200, 302, 303), r.status
        page = await ctx.new_page()
        logs = []
        page.on("console", lambda m: logs.append(f"{m.type}: {m.text[:300]}") if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))
        page.on("requestfailed", lambda rq: logs.append(f"REQFAILED: {rq.url} {rq.failure}"))

        pending = []
        counter = {"n": 0}
        story = {"s": "", "frames": []}

        async def shoot(name, delay_ms):
            await asyncio.sleep(delay_ms / 1000)
            counter["n"] += 1
            n = counter["n"]
            fn = f"{story['s']}_{n:02d}_{name}"
            fn = fn[:90]
            await page.locator("#bezel").screenshot(path=os.path.join(OUT, fn + ".png"))
            dom = await page.frames[1].evaluate(DOM_JS)
            story["frames"].append({"n": n, "name": name, "file": fn + ".png", "dom": dom})

        async def on_keyframe(source, cmd):
            name = keyframe_name(cmd)
            if name:
                delay = 1500 if name == "countdown" else 450
                pending.append(asyncio.create_task(shoot(name, delay)))

        await page.expose_binding("__keyframe", on_keyframe)
        await page.goto(c.BASE + "/sim/", wait_until="load")
        await page.wait_for_function("document.getElementById('conn').dataset.state === 'online'", timeout=20000)
        await page.frame_locator("#face").locator("#eyes").wait_for()
        face = page.frames[1]
        await face.evaluate(RECORDER)
        await page.wait_for_timeout(800)
        if not NOSOUND:
            await page.click("#sound")
            print("audio:", await page.inner_text("#audioState"))
        await page.locator("#bezel").screenshot(path=os.path.join(OUT, "idle.png"))
        await page.screenshot(path=os.path.join(OUT, "idle_fullpage.png"))
        idle_dom = await face.evaluate(DOM_JS)
        json.dump(idle_dom, open(os.path.join(OUT, "idle.dom.json"), "w"), ensure_ascii=False, indent=1)

        for s in STORIES:
            story["s"] = s
            story["frames"] = []
            counter["n"] = 0
            await face.evaluate("window.__rec.length = 0")
            ws0 = await page.evaluate("window.__wsframes.length")
            log0 = len(logs)
            rr = await ctx.request.post(c.BASE + f"/operator/api/scenario/{s}")
            print(s, "POST scenario →", rr.status, await rr.text())
            # wait for the scenario to finish: enough say_end, then 6.5 s with no new face command
            t_end = asyncio.get_event_loop().time() + 120
            last_n, last_change = -1, asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() < t_end:
                rec = await face.evaluate("window.__rec.map(r => r.cmd.type)")
                now = asyncio.get_event_loop().time()
                if len(rec) != last_n:
                    last_n, last_change = len(rec), now
                if rec.count("say_end") >= NEED_SAY_END[s] and now - last_change > 6.5:
                    break
                await asyncio.sleep(0.2)
            else:
                print(s, "TIMEOUT waiting for the end; say_end seen:", rec.count("say_end"))
            if pending:
                await asyncio.gather(*pending)
                pending.clear()
            rec = await face.evaluate("window.__rec")
            t0 = rec[0]["t"] if rec else 0
            trace = [{"t_ms": round(x["t"] - t0), "cmd": x["cmd"]} for x in rec]
            json.dump(trace, open(os.path.join(OUT, f"{s}.trace.json"), "w"), ensure_ascii=False, indent=1)
            ws = (await page.evaluate("window.__wsframes"))[ws0:]
            json.dump(ws, open(os.path.join(OUT, f"{s}.ws.json"), "w"), ensure_ascii=False, indent=1)
            await page.locator("#bezel").screenshot(path=os.path.join(OUT, f"{s}_99_end.png"))
            end_dom = await face.evaluate(DOM_JS)
            story["frames"].append({"n": 99, "name": "end", "file": f"{s}_99_end.png", "dom": end_dom})
            json.dump(story["frames"], open(os.path.join(OUT, f"{s}.keyframes.json"), "w"), ensure_ascii=False, indent=1)
            await page.screenshot(path=os.path.join(OUT, f"{s}_99_end_fullpage.png"))
            simlog = await page.inner_text("#log")
            open(os.path.join(OUT, f"{s}.simlog.txt"), "w").write(simlog)
            print(s, len(trace), "face commands,", len(ws), "ws frames,", len(story["frames"]), "keyframes; page log lines:",
                  logs[log0:][:8])
            await asyncio.sleep(2.5)
        json.dump(logs, open(os.path.join(OUT, "console.json"), "w"), ensure_ascii=False, indent=1)
        await browser.close()


asyncio.run(main())
