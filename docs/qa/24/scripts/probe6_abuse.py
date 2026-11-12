"""QA24 probe 6: input abuse — oversized/malformed JSON, wrong types, unicode, path traversal, huge upload, WS garbage."""
import asyncio, json, time, httpx, websockets

BASE = "http://ahbo-qa24-api:8000"
WS = "ws://ahbo-qa24-api:8000/ws/device"
ENV = dict(l.split("=", 1) for l in open("/scratch/env.txt").read().strip().splitlines())
APP_SECRET = ENV["APP_SECRET"]
INTERNAL_TOKEN = ENV["INTERNAL_TOKEN"]
out = []


def rec(what, r, note=""):
    code = r.status_code if hasattr(r, "status_code") else r
    out.append({"what": what, "code": str(code), "note": note})
    body = ""
    if hasattr(r, "text"):
        body = r.text[:70].replace("\n", " ")
    print(f"{what:62s} -> {code} {body} {note}")


def jpeg(n):
    return b"\xff\xd8\xff\xe0" + b"\x00" * n


def login():
    c = httpx.Client(base_url=BASE, timeout=60, follow_redirects=False)
    c.post("/login", data={"password": "qa-test-password"})
    return c


def http_abuse():
    c = login()
    # --- oversized JSON ---
    big = '{"a":"' + "A" * (25 * 1024 * 1024) + '"}'
    t0 = time.monotonic()
    r = c.post("/events/sensor", content=big, headers={"content-type": "application/json"})
    rec("POST /events/sensor 25MB json (no token)", r, f"{time.monotonic()-t0:.1f}s")
    r = c.post("/operator/api/trigger", content=big, headers={"content-type": "application/json"})
    rec("POST /operator/api/trigger 25MB json (cookie)", r, "expect 4xx not 500")
    # --- malformed / wrong types ---
    r = c.post("/events/sensor", content=b"{not json", headers={"content-type": "application/json"})
    rec("POST /events/sensor malformed json", r)
    r = c.post("/events/sensor", json=[1, 2, 3], headers={"X-Beacon-Token": "qa-beacon-token-1234567890"})
    rec("POST /events/sensor json array", r)
    r = c.post("/events/sensor", json={"beacon_id": 123, "type": "fall_suspected", "seq": "x"},
               headers={"X-Beacon-Token": "qa-beacon-token-1234567890"})
    rec("POST /events/sensor wrong types", r)
    r = c.post("/operator/api/trigger", json={"kind": 123})
    rec("trigger kind=123", r)
    r = c.post("/operator/api/trigger", json={"kind": "fall", "slot_id": {"a": 1}})
    rec("trigger slot_id=object", r)
    r = c.post("/operator/api/trigger", json={"kind": "nonsense_kind"})
    rec("trigger kind=nonsense", r)
    r = c.post("/operator/api/clock", json={"set": {"h": 1}})
    rec("clock set=object", r)
    r = c.post("/operator/api/clock", json={"offset_s": "banana"})
    rec("clock offset_s=banana", r)
    r = c.post("/operator/api/scenario/NOPE")
    rec("scenario name=NOPE", r)
    r = c.get("/operator/api/trace", params={"limit": 99999999})
    rec("trace limit=99999999", r)
    r = c.get("/operator/api/trace", params={"limit": -5})
    rec("trace limit=-5", r)
    r = c.get("/operator/api/trace", params={"correlation_id": "'; DROP TABLE events;--"})
    rec("trace correlation_id=sqlish", r)
    r = c.post("/operator/api/outbox/../../etc/passwd/press", json={"button": "handled"})
    rec("outbox press path traversal", r)
    r = c.get("/operator/api/outbox", params={"limit": "abc"})
    rec("outbox limit=abc", r)
    # unicode / control chars
    weird = "阿寶\u200b\ufeff\u0000\u0004../../\n\r\t\"'\\ 😤 㗎 啱啱" + "重" * 20000
    INT = {"Authorization": f"Bearer {INTERNAL_TOKEN}"}
    r = httpx.post(f"{BASE}/internal/turn", json={"op": "user", "session_id": "s", "turn_id": "t",
                                                  "text": weird, "stt_confidence": 1e9}, headers=INT, timeout=30)
    rec("internal/turn control-char + 20k CJK text", r)
    r = httpx.post(f"{BASE}/internal/tool/{'x' * 5000}", json={"session_id": "s"}, headers=INT, timeout=30)
    rec("internal/tool 5000-char name", r)
    # --- path traversal on /media/* (targets that are NOT .env; never fetch .env) ---
    anon = httpx.Client(base_url=BASE, timeout=20, follow_redirects=False)
    for path in ("/media/fixed/../../brain/pyproject.toml",
                 "/media/fixed/..%2F..%2Fbrain%2Fpyproject.toml",
                 "/media/fixed/%2e%2e%2f%2e%2e%2fbrain%2Fpyproject.toml",
                 "/media/fixed/..\\..\\brain\\pyproject.toml",
                 "/media/fixed/....//....//brain/pyproject.toml",
                 "/media/voice/..%2f..%2frepo%2fpyproject.toml",
                 "/media/screens/../../brain/pyproject.toml",
                 "/media/public/AAAAAAAAAAAAAAAAAAAAAAAA/../../../brain/pyproject.toml",
                 "/media/photo/../../../../etc/passwd",
                 "/media/fixed/F1.m4a%00.png"):
        r = anon.get(path)
        rec(f"GET {path[:52]}", r)
    # --- photo upload abuse ---
    files = {"file": ("evil.jpg", jpeg(9 * 1024 * 1024), "image/jpeg")}
    r = c.post("/media/photo", files=files, data={"incident_id": "inc_x"})
    rec("photo upload 9MB (>8MB cap)", r, "expect 413")
    files = {"file": ("x.txt", b"GIF89a not really", "image/gif")}
    r = c.post("/media/photo", files=files, data={"incident_id": "inc_x"})
    rec("photo upload GIF disguised", r, "expect 415")
    r = c.post("/media/photo", files={"file": ("x.jpg", jpeg(100), "image/jpeg")},
               data={"incident_id": "../../evil"})
    rec("photo incident_id traversal", r, "expect 422")
    r = c.post("/media/photo", files={"file": ("x" * 300 + ".jpg", jpeg(100), "image/jpeg")},
               data={"incident_id": "inc_x"})
    rec("photo upload 300-char filename", r)
    r = c.post("/media/photo", files={"file": ("x.jpg", jpeg(100), "image/jpeg")},
               data={"incident_id": "inc_" + "A" * 200})
    rec("photo incident_id 200 chars", r, "expect 422 (max 64)")
    # login next open-redirect
    r = anon.post("/login", data={"password": "qa-test-password", "next": "//evil.com/x"})
    rec("login next=//evil.com", r, f"loc={r.headers.get('location', '')[:40]} expect local")


async def ws_abuse():
    ws = await websockets.connect(WS, additional_headers={"Authorization": f"Bearer {APP_SECRET}"},
                                  max_size=2**23, open_timeout=5)
    await ws.send(json.dumps({"id": "01HQAPROBE6HELLO000000", "type": "hello", "device_id": "ipad-qa24-abuse",
                              "app_version": "0.1", "fixed_cache_version": "", "wake_engine": "none"}))
    await asyncio.sleep(1.0)
    frames_before = 0
    for payload in ("not json", "[1,2,3]", "123", '"str"', '{"type": 123}', '{"id": "x"}',
                    '{"type": "utterance", "text": 123, "final": "yes"}',
                    '{"type": "utterance", "text": "' + "阿" * 100000 + '", "final": true}',
                    '{"type": "unknown_type", "x": 1}',
                    '{"type": "ack"}', '{"type": "ack", "ref": null}',
                    '{"type": "face_event", "event": "not-a-dict"}',
                    '{"type": "face_event", "event": {"type": "../../reset"}}',
                    '{"type": "heartbeat", "battery": "high", "thermal": 9}',
                    '{"type": "photo_uploaded", "photo_id": 1, "incident_id": null}',
                    '{"type": "error", "code": null, "detail": ' + '"' + "x" * 10000 + '"}'):
        await ws.send(payload)
        await asyncio.sleep(0.05)
    await asyncio.sleep(1.5)
    # is the connection still alive and does a normal turn still work?
    await ws.send(json.dumps({"id": "01HQAPROBE6UTTER000000", "type": "utterance",
                              "text": "阿寶，我幾時食藥呀？", "final": True}))
    got_say = False
    t0 = time.monotonic()
    while time.monotonic() - t0 < 12:
        try:
            f = json.loads(await asyncio.wait_for(ws.recv(), 1.0))
        except (TimeoutError, websockets.exceptions.ConnectionClosed):
            break
        if f.get("type") == "ui" and f.get("ui", {}).get("type") == "say_start":
            got_say = True
            break
    print("WS alive after garbage + normal turn worked:", got_say)
    out.append({"what": "ws survives garbage + normal turn", "code": got_say})
    await ws.close()


if __name__ == "__main__":
    http_abuse()
    asyncio.run(ws_abuse())
    json.dump(out, open("/scratch/abuse_results.json", "w"), indent=1, default=str)
    print("\nWROTE /scratch/abuse_results.json")
