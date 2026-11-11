import asyncio, json
from playwright.async_api import async_playwright
BASE = "http://ahbo-e2e-api:8000"
EXPECT = {  # what must be on the face at some point during the scenario
  "S1": {"cards": ["阿珍嘅錄音"], "steps": ["搵到阿珍嘅語音訊息"], "moods": ["thinking", "warm"], "caption": ["錄音"]},
  "S2": {"cards": ["12:00 食藥", "血壓藥", "糖尿藥", "膽固醇藥", "薄血丸"], "steps": ["下一次：十二點正，四隻藥"], "moods": ["thinking"], "caption": ["十二點正"]},
  "S3": {"cards": ["10:30 覆診", "9:30 出門", "12:30 同阿珍飲茶", "3:00 粵曲班"], "steps": ["今日有兩個約會", "路線已查好", "搵到：三點粵曲班", "下晝得閒，行路十分鐘"], "moods": ["thinking", "curious"], "caption": ["粵曲班"]},
  "S4": {"cards": ["偵測到跌倒", "致電阿珍", "已通知阿珍"], "steps": ["冇回應", "已致電阿珍", "已通知阿珍"], "moods": ["alarm"], "caption": ["打電話俾阿珍"], "okbtn": True},
  "S4ok": {"cards": ["偵測到跌倒"], "steps": ["婆婆話冇事", "唔使致電"], "moods": ["alarm", "warm"], "caption": ["冇事就好"], "okbtn": True},
}
PROBE = """() => { const d = document.querySelector('iframe').contentDocument; return {
  mood: (d.getElementById('face').className || ''), cards: [...d.querySelectorAll('#cards .chip .lbl')].map(e=>e.textContent),
  steps: [...d.querySelectorAll('.step .label')].map(e=>e.textContent), caption: d.getElementById('caption').textContent,
  okbtn: d.getElementById('okbtn').classList.contains('on') } }"""
async def main():
    async with async_playwright() as p:
        b = await p.chromium.connect("ws://ahbo-e2e-browser:3000/")
        ctx = await b.new_context(viewport={"width": 1600, "height": 1000})
        page = await ctx.new_page()
        await page.goto(BASE + "/login"); await page.fill("input[name=password]", "qa-test-password")
        await page.click("button[type=submit], input[type=submit]")
        await page.goto(BASE + "/sim/"); await page.wait_for_timeout(4000)
        results = {}
        for s, exp in EXPECT.items():
            seen = {"cards": set(), "steps": set(), "moods": set(), "caption": "", "okbtn": False}
            req = asyncio.ensure_future(page.request.post(BASE + f"/operator/api/scenario/{s}", timeout=120000))
            for _ in range(130):                                   # sample the face for ~26 s
                await page.wait_for_timeout(200)
                st = await page.evaluate(PROBE)
                seen["cards"] |= set(st["cards"]); seen["steps"] |= set(st["steps"]); seen["caption"] += st["caption"]
                seen["moods"] |= {m for m in ("idle","listening","thinking","curious","warm","alarm","sleepy") if m in st["mood"]}
                seen["okbtn"] |= st["okbtn"]
                if req.done() and _ > 60: break
            r = await req
            await page.screenshot(path=f"/scratch/{s}_end.png")
            miss = [f"card:{c}" for c in exp["cards"] if not any(c in x for x in seen["cards"])]
            miss += [f"step:{c}" for c in exp["steps"] if not any(c in x for x in seen["steps"])]
            miss += [f"mood:{m}" for m in exp["moods"] if m not in seen["moods"]]
            miss += [f"caption:{c}" for c in exp["caption"] if c not in seen["caption"]]
            if exp.get("okbtn") and not seen["okbtn"]: miss.append("ok_button")
            results[s] = {"scenario_api": r.status, "missing": miss}
            print(s, "API", r.status, "->", "PASS" if not miss else f"MISSING {miss}", flush=True)
            await page.wait_for_timeout(2500)
        json.dump(results, open("/scratch/results.json", "w"), ensure_ascii=False, indent=1)
asyncio.run(main())
