"""QA24 probe 3a: start a fall incident and reach AWAITING_OK before the restart."""
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
    await ws.send(json.dumps({"id": "01HQAPROBE3AHELLO00000", "type": "hello", "device_id": "ipad-qa24-restart",
                              "app_version": "0.1.0", "fixed_cache_version": "", "wake_engine": "speech_transcriber"}))
    frames = []

    async def reader():
        try:
            async for raw in ws:
                f = json.loads(raw)
                frames.append(f)
                if f.get("type") in ("ui", "play_fixed", "session.open", "session.close", "wake.enable"):
                    await ws.send(json.dumps({"id": "01HQAPROBE3AACK0000000", "type": "ack", "ref": f.get("id"),
                                              "ok": True}))
        except websockets.exceptions.ConnectionClosed:
            pass

    task = asyncio.get_running_loop().create_task(reader())
    await asyncio.sleep(1.2)
    c.post("/operator/api/trigger", json={"kind": "fall"})
    t0 = time.monotonic()
    while time.monotonic() - t0 < 14:
        await asyncio.sleep(0.5)
        if any(f.get("type") == "ui" and f.get("ui", {}).get("type") == "ok_button"
               and f["ui"].get("show") for f in frames):
            break
    ui = [(f["ui"].get("type"), f["ui"].get("mood") or (f["ui"].get("card") or {}).get("id"),
           f["ui"].get("show") if f["ui"].get("type") == "ok_button" else
           (f["ui"].get("group") or {}).get("id") if f["ui"].get("type") == "steps" else None) for f in frames
          if f.get("type") == "ui"]
    st = c.get("/operator/api/state").json()
    inc = st.get("incident") or {}
    print("pre-restart ui sequence:", ui)
    print("incident:", {k: inc.get(k) for k in ("id", "kind", "state", "source")})
    json.dump({"incident": inc, "ui_count": len(ui)}, open("/scratch/restart_pre.json", "w"), default=str)
    await ws.close()


asyncio.run(main())
