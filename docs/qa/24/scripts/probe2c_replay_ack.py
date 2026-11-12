"""QA24 probe 2c: replay WITH incident state; say_end; step active→done gaps; first ack counts."""
import asyncio, json, time, httpx, websockets

BASE = "http://ahbo-qa24-api:8000"
WS = "ws://ahbo-qa24-api:8000/ws/device"
ENV = dict(l.split("=", 1) for l in open("/scratch/env.txt").read().strip().splitlines())
APP_SECRET = ENV["APP_SECRET"]
results = {}


def log(*a):
    print(*a, flush=True)


class Pad:
    def __init__(self, name, device_id, native=True):
        self.name, self.device_id, self.native = name, device_id, native
        self.frames = []
        self.ws = None

    async def connect(self):
        if self.native:
            hdrs, url = {"Authorization": f"Bearer {APP_SECRET}"}, WS
        else:
            h = httpx.Client(base_url=BASE, timeout=10)
            r = h.post("/login", data={"password": "qa-test-password"})
            hdrs, url = {"Cookie": r.headers["set-cookie"].split(";")[0]}, WS + "?sim=1"
        self.ws = await websockets.connect(url, additional_headers=hdrs, max_size=2**23)
        hello = {"id": "01HQAPROBE2CHELLO000000", "type": "hello", "device_id": self.device_id,
                 "app_version": "0.1.0", "fixed_cache_version": "", "wake_engine": "speech_transcriber"}
        await self.ws.send(json.dumps(hello))
        asyncio.get_running_loop().create_task(self._reader())

    async def close(self):
        try:
            await self.ws.close()
        except Exception:
            pass

    async def _reader(self):
        try:
            async for raw in self.ws:
                self.frames.append((time.monotonic(), json.loads(raw)))
                f = self.frames[-1][1]
                if f.get("type") in ("ui", "play_fixed", "session.open", "session.close", "wake.enable",
                                     "config.update", "capture_photo", "stop_all", "play_voice_message"):
                    await asyncio.sleep(0.02)
                    await self.ws.send(json.dumps({"id": "01HQAPROBE2CACK000000000", "type": "ack",
                                                   "ref": f.get("id"), "ok": True}))
        except websockets.exceptions.ConnectionClosed:
            pass

    def ui_types(self):
        return [(round(t, 2), f["ui"].get("type"), f["ui"]) for t, f in self.frames if f.get("type") == "ui"]


def step_gap_analysis(frames, t0):
    active, gaps = {}, []
    for t, f in frames:
        if f.get("type") != "ui":
            continue
        ui = f["ui"]
        if ui.get("type") == "steps":
            g = ui.get("group") or {}
            for s in g.get("steps", []):
                if s.get("state") == "active":
                    active[(g.get("id"), s.get("id"))] = t
        elif ui.get("type") == "step":
            k = (ui.get("group"), ui.get("id"))
            if ui.get("state") == "active":
                active[k] = t
            elif ui.get("state") in ("done", "failed") and k in active:
                gaps.append({"step": k, "gap_ms": round((t - active[k]) * 1000)})
                active.pop(k, None)
    return gaps


async def main():
    c = httpx.Client(base_url=BASE, timeout=20, follow_redirects=False)
    c.post("/login", data={"password": "qa-test-password"})
    c.post("/operator/api/reset")
    await asyncio.sleep(2.5)

    # ---- A2/A3/A4 on a single pad ----
    p1 = Pad("p1", "ipad-qa24-r1")
    await p1.connect()
    await asyncio.sleep(1.0)
    results["A2_replay_idle"] = [u.get("type") for _, _, u in p1.ui_types()]
    n0 = len(p1.frames)
    await p1.ws.send(json.dumps({"id": "01HQAPROBE2CUTTER000000", "type": "utterance",
                                 "text": "阿寶，我幾時食藥呀？", "final": True}))
    await asyncio.sleep(12.0)
    turn = p1.frames[n0:]
    types = [f.get("type") for _, f in turn]
    ui_seq = [f["ui"].get("type") for _, f in turn if f.get("type") == "ui"]
    results["A3_turn_types"] = {"say_start": ui_seq.count("say_start"), "say_end": ui_seq.count("say_end")}
    log("turn ui sequence:", ui_seq)
    gaps = step_gap_analysis(turn, 0)
    results["A4_step_gaps"] = gaps
    log("step gaps:", gaps)

    # ---- replay DURING an incident (A2b): open second device while fall is active ----
    c.post("/operator/api/trigger", json={"kind": "fall"})
    await asyncio.sleep(4.0)                      # incident running: alarm + card + steps + ok_button soon
    await asyncio.sleep(6.0)                      # let ok_button arrive (F1..F3 clips ~9 s total)
    p2 = Pad("p2", "sim-qa24-r2", native=False)
    await p2.connect()
    await asyncio.sleep(1.5)
    replay = [u for _, _, u in p2.ui_types()]
    results["A2b_replay_incident"] = replay
    log("replay during incident:", [(u.get("type"), u.get("mood") or (u.get("card") or {}).get("id"),
                                     (u.get("group") or {}).get("id"), u.get("show")) for u in replay])
    await p2.close()
    c.post("/operator/api/override", json={"kind": "incident_resolve"})
    await asyncio.sleep(1.5)

    # ---- first ack counts (two devices ack the same command id) ----
    c.post("/operator/api/reset")
    await asyncio.sleep(2.5)
    p3 = Pad("p3", "ipad-qa24-r3")
    p4 = Pad("p4", "sim-qa24-r4", native=False)
    await p3.connect(); await p4.connect()
    await asyncio.sleep(1.0)
    st = c.get("/operator/api/state").json()
    lat_before = {d["device_id"]: d.get("ack_latency_ms") for d in st.get("devices", [])}
    n3 = len(p3.frames)
    await p3.ws.send(json.dumps({"id": "01HQAPROBE2CUTTR20000000", "type": "utterance",
                                 "text": "今日有咩活動呀？", "final": True}))
    await asyncio.sleep(8.0)
    shared = {f.get("id") for _, f in p3.frames[n3:] if f.get("type") == "ui"}
    # both pads ack one shared command id (p4's reader would auto-ack too — pick one and count kernel ack events)
    target = sorted(shared)[0] if shared else None
    tr = c.get("/operator/api/trace", params={"limit": 400}).json()
    acks = [e for e in tr["events"] if e["type"] == "device.ack"]
    st2 = c.get("/operator/api/state").json()
    lat_after = {d["device_id"]: (d.get("ack_latency_ms"), d.get("online")) for d in st2.get("devices", [])}
    results["C_ack"] = {"lat_before": lat_before, "lat_after": lat_after, "ack_events": len(acks)}
    log("ack latency before:", lat_before)
    log("ack latency after:", lat_after)
    log("device.ack kernel events (last turn):", len(acks))
    await p3.close(); await p4.close()
    json.dump(results, open("/scratch/replay_ack_results.json", "w"), indent=1, default=str)
    log("WROTE /scratch/replay_ack_results.json")


asyncio.run(main())
