"""QA24 probe 2b: exact P0 resend timeline for the fall ok_button batch (no acks at all)."""
import asyncio, json, time, httpx, websockets

BASE = "http://ahbo-qa24-api:8000"
WS = "ws://ahbo-qa24-api:8000/ws/device"
ENV = dict(l.split("=", 1) for l in open("/scratch/env.txt").read().strip().splitlines())
APP_SECRET = ENV["APP_SECRET"]


async def main():
    c = httpx.Client(base_url=BASE, timeout=15, follow_redirects=False)
    c.post("/login", data={"password": "qa-test-password"})
    c.post("/operator/api/reset")
    await asyncio.sleep(2.5)

    ws = await websockets.connect(WS, additional_headers={"Authorization": f"Bearer {APP_SECRET}"}, max_size=2**23)
    await ws.send(json.dumps({"id": "01HQAPROBE2BHELLO000000", "type": "hello", "device_id": "ipad-qa24-p0",
                              "app_version": "0.1.0", "fixed_cache_version": "", "wake_engine": "none"}))

    frames = []
    stop = asyncio.Event()

    async def reader():
        try:
            async for raw in ws:
                f = json.loads(raw)
                frames.append((time.monotonic(), f.get("id"), f.get("type"), f.get("priority"),
                               (f.get("ui") or {}).get("type") if f.get("type") == "ui" else f.get("clip_id")))
        except websockets.exceptions.ConnectionClosed:
            pass

    task = asyncio.get_running_loop().create_task(reader())
    # drain welcome + replay
    await asyncio.sleep(1.5)
    n0 = len(frames)
    trigger_wall = datetime_now = time.time()
    c.post("/operator/api/trigger", json={"kind": "fall"})
    t0 = time.monotonic()
    await asyncio.sleep(30)
    stop.set()
    task.cancel()

    print(f"trigger at monotonic {t0:.3f}; wall {trigger_wall:.3f}")
    seen = {}
    for t, fid, ty, pr, extra in frames:
        seen.setdefault(fid, []).append(round(t - t0, 2))
    print("total frames:", len(frames), "unique ids:", len(seen))
    for fid, times in seen.items():
        flag = ""
        if len(times) > 4:
            flag = "  << MORE THAN initial+3resends"
        print(f"{fid}  n={len(times)} at {times}{flag}")
    json.dump({k: v for k, v in seen.items()}, open("/scratch/p0_timeline.json", "w"), indent=1)
    st = c.get("/operator/api/state").json()
    print("devices:", [(d.get("device_id"), d.get("unacked_p0")) for d in st.get("devices", [])])
    await ws.close()


asyncio.run(main())
