"""Capture the face kit's storyboards (face/dev.html) as golden UI traces + keyframe screenshots.

Runs inside the `browser` container (Playwright image). Usage:
    python evals/visual/kit_capture.py --base http://127.0.0.1:8766 --out docs/screens/kit \
        --traces brain/tests/golden/kit

For each storyboard (S1, S2, S3, S4, S4ok) it:
  * records every command dev.html posts to the face iframe (the golden trace), in order;
  * takes a keyframe screenshot of the face after each say_end, countdown, ok_button and alarm.
The traces are what brain/tests/test_golden_ui.py compares the Kernel against (§23.8).
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

STORIES = ["S1", "S2", "S3", "S4", "S4ok"]

RECORDER = """
(() => {
  if (window.__rec) return;
  window.__rec = [];
  addEventListener("message", ev => {
    if (ev.data && ev.data.ahbo) {
      const c = ev.data.ahbo;
      window.__rec.push({t: performance.now(), cmd: c});
      if (window.__keyframe) window.__keyframe(c);
    }
  });
})();
"""


def keyframe_name(cmd: dict) -> str | None:
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


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8766")
    ap.add_argument("--out", default="docs/screens/kit")
    ap.add_argument("--traces", default="brain/tests/golden/kit")
    args = ap.parse_args()
    out, traces = Path(args.out), Path(args.traces)
    out.mkdir(parents=True, exist_ok=True)
    traces.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1600, "height": 1000})
        pending: list[asyncio.Task] = []
        counter = {"n": 0}
        story_name = {"s": ""}

        async def shoot(name: str, delay_ms: int) -> None:
            await asyncio.sleep(delay_ms / 1000)
            counter["n"] += 1
            el = page.locator("#bezel")
            await el.screenshot(path=str(out / f"{story_name['s']}_{counter['n']:02d}_{name}.png"))

        async def on_keyframe(source, cmd):  # noqa: ARG001
            name = keyframe_name(cmd)
            if name:
                delay = 1500 if name == "countdown" else 450
                pending.append(asyncio.create_task(shoot(name, delay)))

        await page.expose_binding("__keyframe", on_keyframe)
        await page.goto(f"{args.base}/dev.html")
        frame = page.frame_locator("#face")
        await frame.locator("#eyes").wait_for()
        face = page.frames[1]
        await face.evaluate(RECORDER)
        await page.locator("#bezel").screenshot(path=str(out / "idle.png"))

        for s in STORIES:
            story_name["s"] = s
            counter["n"] = 0
            await face.evaluate("window.__rec.length = 0")
            await page.evaluate(f"document.querySelector('[data-story={s}]').onclick()")
            await asyncio.sleep(0.5)
            if pending:
                await asyncio.gather(*pending)
                pending.clear()
            rec = await face.evaluate("window.__rec")
            t0 = rec[0]["t"] if rec else 0
            trace = [{"t_ms": round(r["t"] - t0), "cmd": r["cmd"]} for r in rec]
            (traces / f"{s}.json").write_text(json.dumps(trace, ensure_ascii=False, indent=1))
            await page.locator("#bezel").screenshot(path=str(out / f"{s}_99_end.png"))
            print(s, len(trace), "commands")
            await asyncio.sleep(2.5)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
