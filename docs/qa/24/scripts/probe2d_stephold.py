"""QA24 probe 2d: full-frame dump of one fake turn to verify the 600 ms step hold (§23.6)."""
import asyncio, json, time, httpx, websockets

BASE = "http://ahbo-qa24-api:8000"
WS = "ws://ahbo-qa24-api:8000/ws/device"
ENV = dict(l.split("=", 1) for l in open("/scratch/env.txt").read().strip().splitlines())
APP_SECRET = ENV["APP_SECRET"]


async def one_turn(c, text, label):
    ws = await websockets.connect(WS, additional_headers={"Authorization": f"Bearer {APP_SECRET}"}, max_size=2**23)
    await ws.send(json.dumps({"id": "01HQAPROBE2DHELLO000000", "type": "hello", "device_id": f"ipad-qa24-{label}",
                              "app_version": "0.1.0", "fixed_cache_version": "", "wake_engine": "none"}))
    frames = []

    async def reader():
        try:
            async for raw in ws:
                f = json.loads(raw)
                frames.append((time.monotonic(), f))
                if f.get("type") in ("ui", "play_fixed", "session.open", "session.close", "wake.enable"):
                    await ws.send(json.dumps({"id": "01HQAPROBE2DACK000000000", "type": "ack",
                                              "ref": f.get("id"), "ok": True}))
        except websockets.exceptions.ConnectionClosed:
            pass

    task = asyncio.get_running_loop().create_task(reader())
    await asyncio.sleep(1.2)                     # welcome + replay
    n0 = len(frames)
    await ws.send(json.dumps({"id": "01HQAPROBE2DUTTER000000", "type": "utterance", "text": text, "final": True}))
    await asyncio.sleep(10.0)
    task.cancel()
    await ws.close()
    print(f"--- turn '{text}' ({label}) ---")
    for t, f in frames[n0:]:
        if f.get("type") == "ui" and f["ui"].get("type") in ("steps", "step"):
            print(f"  +{ (t - frames[n0][0]) * 1000:7.1f} ms  {json.dumps(f['ui'], ensure_ascii=False)}")
    # gap between steps-frame send and step-done receive
    t_steps = next((t for t, f in frames[n0:] if f.get("type") == "ui" and f["ui"].get("type") == "steps"), None)
    t_done = next((t for t, f in frames[n0:] if f.get("type") == "ui" and f["ui"].get("type") == "step"
                   and f["ui"].get("state") in ("done", "failed")), None)
    if t_steps and t_done:
        print(f"  active→done wire gap: {(t_done - t_steps) * 1000:.1f} ms (contract: ≥600)")


async def main():
    c = httpx.Client(base_url=BASE, timeout=15, follow_redirects=False)
    c.post("/login", data={"password": "qa-test-password"})
    c.post("/operator/api/reset")
    await asyncio.sleep(2.5)
    for i, (txt, lab) in enumerate([("阿寶，我幾時食藥呀？", "med"), ("今日有咩約會呀？", "ev"), ("而家幾多點呀？", "time")]):
        await one_turn(c, txt, lab)
        await asyncio.sleep(1.0)


asyncio.run(main())
