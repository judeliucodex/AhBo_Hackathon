"""QA24 probe 2: device channel — scripted fake iPad over real WebSockets.

Phases:
  A  hello → welcome fields; replay after welcome; utterance starts a text turn; step active→done ≥600 ms
  B  P0 command without ack → resent ≤3× (same id), then failed + unacked_p0; ack → cleared
  C  two devices: both get commands; first ack counts; playback_done de-duplicated by say_id
  D  disconnect 30 s → device.offline; reset demo timing (≤5 s)
"""
import asyncio, json, time, httpx, websockets

BASE = "http://ahbo-qa24-api:8000"
WS = "ws://ahbo-qa24-api:8000/ws/device"
ENV = dict(l.split("=", 1) for l in open("/scratch/env.txt").read().strip().splitlines())
APP_SECRET = ENV["APP_SECRET"]
PASSWORD = "qa-test-password"
log_f = open("/scratch/device_channel.log", "w")


def log(*a):
    line = " ".join(str(x) for x in a)
    print(line, flush=True)
    log_f.write(line + "\n")
    log_f.flush()


def op_client():
    c = httpx.Client(base_url=BASE, timeout=15, follow_redirects=False)
    r = c.post("/login", data={"password": PASSWORD})
    return c  # cookie jar holds the session


class Pad:
    """A fake iPad. If ack_all=True it answers every command with ack within ~50 ms."""

    def __init__(self, name, *, native=True, ack_all=True, device_id=None):
        self.name = name
        self.native = native
        self.ack_all = ack_all
        self.device_id = device_id or ("ipad-" + name.lower())
        self.frames = []            # (t, frame)
        self.seen_ids = set()
        self.ws = None
        self.welcome = None
        self.task = None

    async def connect(self):
        hdrs = {"Authorization": f"Bearer {APP_SECRET}"} if self.native else {}
        url = WS + ("" if self.native else "?sim=1")
        if not self.native:
            c = httpx.Client(base_url=BASE, timeout=10)
            r = c.post("/login", data={"password": PASSWORD})
            jar = r.headers["set-cookie"].split(";")[0]
            hdrs = {"Cookie": jar}
        self.ws = await websockets.connect(url, additional_headers=hdrs, open_timeout=5, max_size=2**23)
        hello = {"id": "01HQATESTHELLO0000000000", "type": "hello", "device_id": self.device_id,
                 "app_version": "0.1.0", "fixed_cache_version": "", "wake_engine": "speech_transcriber"}
        await self.ws.send(json.dumps(hello))
        self.task = asyncio.get_running_loop().create_task(self._reader())

    async def _reader(self):
        try:
            async for raw in self.ws:
                f = json.loads(raw)
                self.frames.append((time.monotonic(), f))
                log(f"  [{self.name}] ← {f.get('type'):18s} id={str(f.get('id'))[:12]} prio={f.get('priority')}"
                    f" {({k: v for k, v in f.items() if k not in ('id', 'type', 'priority')}).__repr__()[:110]}")
                if f.get("type") == "welcome":
                    self.welcome = f
                    continue
                if f.get("type") == "play_fixed":
                    # the iPad would play the clip; send say_start/say_end semantics via playback_done
                    pass
                if self.ack_all and f.get("type") in ("ui", "play_fixed", "session.open", "session.close",
                                                       "wake.enable", "config.update", "stop_all",
                                                       "capture_photo", "play_voice_message"):
                    await asyncio.sleep(0.02)
                    await self.send({"id": "01HQATESTACK0000000000" + str(len(self.frames)), "type": "ack",
                                     "ref": f.get("id"), "ok": True})
        except websockets.exceptions.ConnectionClosed as e:
            log(f"  [{self.name}] closed code={e.rcvd.code if e.rcvd else '?'}")

    async def send(self, obj):
        obj.setdefault("id", "01HQATESTEV000000000" + str(int(time.time() * 1000) % 10**8))
        await self.ws.send(json.dumps(obj))

    def frames_of(self, *types):
        return [(t, f) for (t, f) in self.frames if f.get("type") in types]

    async def close(self):
        try:
            await self.ws.close()
        except Exception:
            pass


def step_keys(ui):
    t = ui.get("type")
    if t == "step":
        return (str(ui.get("group")), str(ui.get("id"))), ui.get("state")
    if t == "steps":
        g = ui.get("group") or {}
        return ([(str(g.get("id")), str(s.get("id"))) for s in g.get("steps", [])], None)
    return None, None


async def phase_a(pad, c):
    log("== Phase A: hello/welcome/replay/utterance/step timing ==")
    # welcome fields
    while pad.welcome is None:
        await asyncio.sleep(0.05)
    w = pad.welcome
    ok = {}
    ok["device_token dt.*"] = str(w.get("device_token", "")).startswith("dt.")
    ok["server_time +08:00"] = "server_time" in w and ("+08:00" in w["server_time"])
    ok["fixed_cache_version"] = bool(w.get("fixed_cache_version"))
    ok["text_sessions"] = w.get("text_sessions") is True
    cfg = w.get("config") or {}
    ok["config.quiet_hours"] = isinstance(cfg.get("quiet_hours"), dict) and "start" in cfg["quiet_hours"]
    ok["config.interruption_enabled"] = isinstance(cfg.get("interruption_enabled"), bool)
    ok["config.wake"] = isinstance(cfg.get("wake"), dict) and len(cfg.get("wake", {})) >= 3
    ok["config.reminder_mirror"] = isinstance(cfg.get("reminder_mirror"), dict) and cfg["reminder_mirror"].get("slots")
    log("   welcome field checks:", json.dumps(ok))
    bad = [k for k, v in ok.items() if not v]
    log("   RESULT A1 welcome fields:", "PASS" if not bad else f"FAIL missing={bad}")

    # replay after welcome: first ui frame should be reset, then mood, then persistent cards
    ui_frames = [f["ui"] for _, f in pad.frames_of("ui")]
    seq = [u.get("type") for u in ui_frames]
    log("   replay ui sequence:", seq[:12])
    a2 = seq and seq[0] == "reset" and "mood" in seq[:3]
    log("   RESULT A2 replay starts reset then mood:", "PASS" if a2 else f"FAIL {seq[:6]}")

    # utterance starts a text turn
    n_before = len(pad.frames)
    await pad.send({"type": "utterance", "text": "阿寶，我幾時食藥呀？", "final": True})
    t0 = time.monotonic()
    while time.monotonic() - t0 < 15:
        await asyncio.sleep(0.2)
        new = pad.frames[n_before:]
        if any(f.get("type") == "ui" and f.get("ui", {}).get("type") == "say_start" for _, f in new):
            break
    new = pad.frames[n_before:]
    types = [f.get("type") for _, f in new]
    say = [f["ui"] for _, f in new if f.get("type") == "ui" and f.get("ui", {}).get("type") == "say_start"]
    log("   turn frames:", types)
    log("   reply caption:", (say[-1].get("text") if say else None))
    a3 = bool(say) and ("say_end" in [u.get("type") for _, f in new if f.get("type") == "ui"
                                       for u in [f["ui"]]])
    log("   RESULT A3 utterance starts text turn with say_start/say_end:", "PASS" if a3 else "FAIL")

    # step active → done ≥ 600 ms
    active_t, done_t = {}, {}
    for ts, f in pad.frames:
        if f.get("type") != "ui":
            continue
        ui = f["ui"]
        if ui.get("type") == "step":
            k, st = step_keys(ui)
            if st == "active":
                active_t[k] = ts
            elif st in ("done", "failed"):
                done_t[k] = ts
    gaps = {f"{k[0]}/{k[1]}": round((done_t[k] - active_t[k]) * 1000) for k in done_t if k in active_t}
    a4 = bool(gaps) and all(v >= 600 for v in gaps.values())
    log("   step active→done gaps (ms):", gaps)
    log("   RESULT A4 step done held ≥600 ms after active:", "PASS" if a4 else f"FAIL {gaps}")
    return {"welcome": ok, "replay": a2, "utterance_turn": a3, "step_gaps_ms": gaps}


async def phase_b(pad, c):
    log("== Phase B: P0 resend ≤3× when not acked ==")
    r = c.post("/operator/api/reset")
    await asyncio.sleep(2.0)
    pad.frames.clear()
    pad.seen_ids.clear()
    r = c.post("/operator/api/trigger", json={"kind": "fall"})
    log("   trigger fall:", r.status_code, r.text[:80])
    # watch 12 s: no acks (stop acking) — record repeats of the same frame id
    pad.ack_all = False
    t0 = time.monotonic()
    while time.monotonic() - t0 < 12:
        await asyncio.sleep(0.3)
    counts = {}
    first_seen = {}
    for ts, f in pad.frames:
        fid = f.get("id")
        counts[fid] = counts.get(fid, 0) + 1
        first_seen.setdefault(fid, []).append(round(ts - pad.frames[0][0], 2))
    repeats = {k: (v, first_seen[k]) for k, v in counts.items() if v > 1}
    log("   frames seen >1× (count, times s):", json.dumps(repeats))
    st = c.get("/operator/api/state").json()
    devs = st.get("devices", [])
    unacked = [(d.get("device_id"), d.get("unacked_p0")) for d in devs]
    log("   devices snapshot unacked_p0:", unacked)
    max_resends = max((v - 1 for v, _ in repeats.values()), default=0)
    same_id = all(len({f.get("id") for _, f in pad.frames}) < len(pad.frames) for _ in [0])
    b1 = 1 <= max_resends <= 3
    log("   RESULT B1 P0 resent same id ≤3×:", "PASS" if b1 else f"FAIL max_resends={max_resends}")
    # now ack one of the resent P0 frames → unacked_p0 decrements
    p0ids = [fid for fid, f2 in [(k, None) for k in counts] ]
    # find a P0 frame id (priority 0)
    p0 = [f.get("id") for _, f in pad.frames if f.get("priority") == 0]
    if p0:
        await pad.send({"type": "ack", "ref": p0[0], "ok": True})
        await asyncio.sleep(1.0)
        st2 = c.get("/operator/api/state").json()
        unacked2 = [(d.get("device_id"), d.get("unacked_p0")) for d in st2.get("devices", [])]
        log("   after ack unacked_p0:", unacked2)
    # cleanup: resolve incident via operator override incident_resolve
    c.post("/operator/api/override", json={"kind": "incident_resolve"})
    await asyncio.sleep(1.0)
    return {"max_resends": max_resends, "unacked_after_ack": unacked2 if p0 else None}


async def phase_c(pads, c):
    log("== Phase C: two devices, first ack counts, playback_done dedup ==")
    p1, p2 = pads
    c.post("/operator/api/reset")
    await asyncio.sleep(2.5)
    for p in pads:
        p.frames.clear()
    c.post("/operator/api/trigger", json={"kind": "fall"})
    await asyncio.sleep(4.0)
    ids1 = {f.get("id") for _, f in p1.frames if f.get("type") != "welcome"}
    ids2 = {f.get("id") for _, f in p2.frames if f.get("type") != "welcome"}
    both = ids1 & ids2
    log(f"   frames on ipad1={len(ids1)} ipad2={len(ids2)} shared={len(both)}")
    c1 = all(i in ids2 for i in ids1 if i in both)
    # find a play_fixed clip and its say_id, or a ui; send playback_done twice from both pads
    pf = [f for _, f in p1.frames if f.get("type") == "play_fixed"]
    if pf:
        say_id = pf[0].get("say_id")
        clip_id = pf[0].get("clip_id")
        log(f"   sending playback_done say_id={say_id} clip_id={clip_id} from BOTH devices")
        for p in pads:
            await p.send({"type": "playback_done", "clip_id": clip_id, "say_id": say_id})
        await asyncio.sleep(1.5)
        tr = c.get("/operator/api/trace", params={"limit": 300}).json()
        pbd = [e for e in tr["events"] if e["type"] == "device.playback_done"]
        log(f"   device.playback_done kernel events after both sent: {len(pbd)}")
        dedup = len(pbd) == 1
        log("   RESULT C2 playback_done de-duplicated by say_id:", "PASS" if dedup else f"FAIL n={len(pbd)}")
    else:
        log("   RESULT C2: no play_fixed observed — SKIPPED")
        dedup = None
    # first ack counts: both ack the same id; ack latency recorded once (info dict) — check trace device.ack
    shared_cmd = sorted(both)[0] if both else None
    if shared_cmd:
        for p in pads:
            await p.send({"type": "ack", "ref": shared_cmd, "ok": True})
        await asyncio.sleep(1.0)
        tr = c.get("/operator/api/trace", params={"limit": 300}).json()
        acks = [e for e in tr["events"] if e["type"] == "device.ack" and
                str(e.get("payload", {}).get("ref", "")).endswith(shared_cmd[-12:])]
        log(f"   device.ack kernel events for one shared command id: {len(acks)}")
    c.post("/operator/api/override", json={"kind": "incident_resolve"})
    await asyncio.sleep(1.0)
    return {"both_devices_shared": len(both), "playback_dedup": dedup}


async def phase_d(pads, c):
    log("== Phase D: disconnect 30 s → device.offline; reset timing ==")
    p2 = pads[1]
    tr = c.get("/operator/api/trace", params={"limit": 50}).json()
    before = len([e for e in tr["events"] if e["type"] == "device.offline"])
    t0 = time.monotonic()
    await p2.close()
    log(f"   ipad2 ({p2.device_id}) disconnected; waiting 32 s for the offline timer")
    await asyncio.sleep(32)
    tr = c.get("/operator/api/trace", params={"limit": 100}).json()
    offs = [e for e in tr["events"] if e["type"] == "device.offline"]
    new_offs = offs  # events are newest-first; count events mentioning device
    st = c.get("/operator/api/state").json()
    d2 = [d for d in st.get("devices", []) if d.get("device_id") == p2.device_id]
    log(f"   device.offline events total={len(offs)} (before={before}); state row={d2}")
    d1 = len(offs) > before
    log("   RESULT D1 disconnect → device.offline after 30 s:", "PASS" if d1 else "FAIL")
    # reset timing (check 3 part): reset demo ≤ 5 s
    t0 = time.monotonic()
    r = c.post("/operator/api/reset")
    ms = (time.monotonic() - t0) * 1000
    log(f"   POST /operator/api/reset -> {r.status_code} {r.text[:80]} took {ms:.0f} ms")
    log("   RESULT D2 reset demo ≤5 s:", "PASS" if r.status_code == 200 and ms < 5000 else "FAIL")
    return {"offline_event": d1, "reset_ms": round(ms)}


async def main():
    c = op_client()
    c.post("/operator/api/reset")
    await asyncio.sleep(2.0)
    results = {}
    pad1 = Pad("iPad1", native=True, ack_all=True, device_id="ipad-qa24-1")
    await pad1.connect()
    results["A"] = await phase_a(pad1, c)
    results["B"] = await phase_b(pad1, c)
    pad2 = Pad("iPad2-sim", native=False, ack_all=True, device_id="sim-qa24-2")
    await pad2.connect()
    await asyncio.sleep(1.0)
    results["C"] = await phase_c([pad1, pad2], c)
    results["D"] = await phase_d([pad1, pad2], c)
    await pad1.close()
    await pad2.close()
    json.dump(results, open("/scratch/device_results.json", "w"), indent=1, default=str)
    log("\nWROTE /scratch/device_results.json")


asyncio.run(main())
