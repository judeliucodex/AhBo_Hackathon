"""QA24 probe 3b: after restart — reconnect, check replay + escalation + touch_ok resolution."""
import asyncio, json, time, httpx, websockets

BASE = "http://ahbo-qa24-api:8000"
WS = "ws://ahbo-qa24-api:8000/ws/device"
ENV = dict(l.split("=", 1) for l in open("/scratch/env.txt").read().strip().splitlines())
APP_SECRET = ENV["APP_SECRET"]


async def main():
    c = httpx.Client(base_url=BASE, timeout=15, follow_redirects=False)
    c.post("/login", data={"password": "qa-test-password"})
    ws = await websockets.connect(WS, additional_headers={"Authorization": f"Bearer {APP_SECRET}"}, max_size=2**23)
    await ws.send(json.dumps({"id": "01HQAPROBE3BHELLO00000", "type": "hello", "device_id": "ipad-qa24-restart",
                              "app_version": "0.1.0", "fixed_cache_version": "", "wake_engine": "speech_transcriber"}))
    frames = []

    async def reader():
        try:
            async for raw in ws:
                f = json.loads(raw)
                frames.append((round(time.monotonic() % 1000, 2), f))
                if f.get("type") in ("ui", "play_fixed", "session.open", "session.close", "wake.enable"):
                    await ws.send(json.dumps({"id": "01HQAPROBE3BACK00000000", "type": "ack", "ref": f.get("id"),
                                              "ok": True}))
        except websockets.exceptions.ConnectionClosed:
            pass

    task = asyncio.get_running_loop().create_task(reader())
    await asyncio.sleep(1.5)                     # welcome + replay
    replay = [(f["ui"].get("type"), f["ui"].get("mood") or (f["ui"].get("card") or {}).get("id"),
               f["ui"].get("show") if f["ui"].get("type") == "ok_button" else
               ((f["ui"].get("group") or {}).get("id"), [s.get("state") for s in (f["ui"].get("group") or {}).get("steps", [])])
               if f["ui"].get("type") == "steps" else None)
              for _, f in frames if f.get("type") == "ui"]
    print("POST-RESTART REPLAY:", replay)
    results = {"replay": replay}

    # wait for the resumed countdown → escalation (fresh countdown 5 s + escalation)
    t0 = time.monotonic()
    escalated = False
    while time.monotonic() - t0 < 25:
        await asyncio.sleep(1.0)
        st = c.get("/operator/api/state").json()
        inc = st.get("incident") or {}
        if inc.get("state") in ("ESCALATING", "REASSURING", "ACKNOWLEDGED"):
            escalated = True
            print(f"escalated after restart: state={inc.get('state')} at +{time.monotonic() - t0:.1f} s")
            break
    results["escalated_after_restart"] = escalated
    if escalated:
        ob = c.get("/operator/api/outbox").json()
        rows = [(r.get("channel"), r.get("kind"), r.get("status"), r.get("to_contact")) for r in ob.get("items", [])][:6]
        print("outbox rows:", rows)
        results["outbox"] = rows
    # now press OK via the device button (touch_ok)
    await ws.send(json.dumps({"id": "01HQAPROBE3BTOUCH000000", "type": "touch_ok"}))
    await asyncio.sleep(3.0)
    st = c.get("/operator/api/state").json()
    inc = st.get("incident")
    print("after touch_ok: incident =", None if not inc else inc.get("state"))
    results["after_touch_ok"] = inc.get("state") if inc else None
    ui_after = [f["ui"].get("type") for _, f in frames[-8:] if f.get("type") == "ui"]
    print("last ui frames:", ui_after)
    await ws.close()
    json.dump(results, open("/scratch/restart_results.json", "w"), indent=1, default=str)


asyncio.run(main())
