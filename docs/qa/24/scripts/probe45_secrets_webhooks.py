"""QA24 probe 4+5: secrets leakage scan + webhook auth (telegram / twilio / beacon)."""
import asyncio, hashlib, hmac, json, base64, time, httpx, websockets

BASE = "http://ahbo-qa24-api:8000"
ENV = dict(l.split("=", 1) for l in open("/scratch/env.txt").read().strip().splitlines())
APP_SECRET, INTERNAL_TOKEN = ENV["APP_SECRET"], ENV["INTERNAL_TOKEN"]
TG_SECRET = ENV["TELEGRAM_WEBHOOK_SECRET"]
BEACON_TOKEN = "qa-beacon-token-1234567890"
SID, AUTH_TOKEN, FROM = ENV["TWILIO_ACCOUNT_SID"], ENV["TWILIO_AUTH_TOKEN"], ENV["TWILIO_FROM"]
results = {}


def scan(name, text):
    """Look for any secret material beyond the last 4 chars."""
    hits = []
    needles = {
        "APP_SECRET": APP_SECRET, "INTERNAL_TOKEN": INTERNAL_TOKEN, "SESSION_SECRET": ENV["SESSION_SECRET"],
        "TELEGRAM_WEBHOOK_SECRET": TG_SECRET, "BEACON_TOKEN": BEACON_TOKEN, "TWILIO_AUTH_TOKEN": AUTH_TOKEN,
        "TWILIO_SID": SID,
    }
    for label, val in needles.items():
        if val and len(val) > 4:
            if val[:-4] in text:                      # all but the last 4 chars present
                hits.append(label)
    return hits


def login():
    c = httpx.Client(base_url=BASE, timeout=20, follow_redirects=False)
    c.post("/login", data={"password": "qa-test-password"})
    return c


# ---------------- check 4: secrets ----------------
def check4():
    found = {}
    # 1. server logs (docker logs read from inside? no: run on host separately; here scan what we can see via API)
    # 2. /operator/api/state
    c = login()
    st = json.dumps(c.get("/operator/api/state").json())
    found["operator/api/state"] = scan("state", st)
    # 3. /operator/api/health
    h = json.dumps(c.get("/operator/api/health").json())
    found["operator/api/health"] = scan("health", h)
    # 4. /operator/stream for ~32 s while a device session drives traffic
    import threading

    def drive():
        async def go():
            async with websockets.connect("ws://ahbo-qa24-api:8000/ws/device",
                                          additional_headers={"Authorization": f"Bearer {APP_SECRET}"},
                                          max_size=2**23) as ws:
                await ws.send(json.dumps({"id": "01HQAPROBE45HELLO0000", "type": "hello",
                                          "device_id": "ipad-qa24-sec", "app_version": "0.1",
                                          "fixed_cache_version": "", "wake_engine": "none"}))
                await asyncio.sleep(2)
                await ws.send(json.dumps({"id": "01HQAPROBE45UTTER000", "type": "utterance",
                                          "text": "阿寶，我幾時食藥呀？", "final": True}))
                await asyncio.sleep(10)
                await ws.send(json.dumps({"id": "01HQAPROBE45HB00000000", "type": "heartbeat",
                                          "battery": 0.8, "thermal": "nominal", "in_session": False}))
                await asyncio.sleep(16)
        asyncio.run(go())

    th = threading.Thread(target=drive)
    th.start()
    buf = []
    with c.stream("GET", "/operator/stream") as r:
        start = time.monotonic()
        for line in r.iter_lines():
            buf.append(line)
            if time.monotonic() - start > 30:
                break
    th.join()
    sse = "\n".join(buf)
    open("/scratch/sse_dump.txt", "w").write(sse[:200000])
    # 5. telegram /health reply via the real webhook path (admin chat id 987654321)
    upd = {"update_id": 4242, "message": {"message_id": 1, "date": int(time.time()),
                                          "chat": {"id": 987654321, "type": "private"},
                                          "from": {"id": 987654321, "first_name": "Ah Chun"},
                                          "text": "/health"}}
    r = httpx.post(f"{BASE}/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": TG_SECRET}, json=upd,
                   timeout=30)
    body = r.text
    found["telegram /health reply"] = scan("tg", body)
    open("/scratch/tg_health_reply.txt", "w").write(body)
    print("telegram /health reply status:", r.status_code, "len:", len(body))
    print("reply head:", body[:300])
    results["check4"] = found
    print("CHECK4 secrets found:", json.dumps(found))


# ---------------- check 5: webhooks ----------------
def twilio_signature(url, params):
    """Twilio validator algorithm: HMAC-SHA1(auth_token, url + sorted concatenated k v) → base64."""
    data = url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
    mac = hmac.new(AUTH_TOKEN.encode(), data.encode(), hashlib.sha1).digest()
    return base64.b64encode(mac).decode()


def check5():
    out = {}
    c = httpx.Client(base_url=BASE, timeout=15, follow_redirects=False)
    # telegram
    upd = {"update_id": 5, "message": {"message_id": 2, "date": int(time.time()),
                                       "chat": {"id": 111, "type": "private"}, "from": {"id": 111}, "text": "hello"}}
    r = c.post("/telegram/webhook", json=upd)
    out["tg_no_secret"] = (r.status_code, r.text[:60])
    r = c.post("/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"}, json=upd)
    out["tg_wrong_secret"] = (r.status_code, r.text[:60])
    r = httpx.post(f"{BASE}/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": TG_SECRET},
                   json={"update_id": 6}, timeout=15)
    out["tg_right_secret_empty_update"] = (r.status_code, r.text[:60])
    # twilio — valid signature over the exact URL the server sees
    url = f"{BASE}/twilio/twiml/CAtestcall1"
    params = {"CallSid": "CAxxx", "From": FROM, "To": "+85270000000"}
    sig = twilio_signature(url, params)
    r = c.post("/twilio/twiml/CAtestcall1", data=params)
    out["twiml_no_sig"] = (r.status_code, r.text[:60])
    r = c.post("/twilio/twiml/CAtestcall1", data=params, headers={"X-Twilio-Signature": "bad=="})
    out["twiml_bad_sig"] = (r.status_code, r.text[:60])
    r = c.post("/twilio/twiml/CAtestcall1", data=params, headers={"X-Twilio-Signature": sig})
    out["twiml_valid_sig"] = (r.status_code, r.text[:100])
    # valid signature over the wrong URL (path confusion)
    url2 = f"{BASE}/twilio/gather"
    sig2 = twilio_signature(url2, params)
    r = c.post("/twilio/gather", data=params, headers={"X-Twilio-Signature": sig2})
    out["gather_valid_sig"] = (r.status_code, r.text[:80])
    # gather with Digits=1 (press-1 ack) using a valid signature
    sig3 = twilio_signature(url2, {**params, "Digits": "1"})
    r = c.post("/twilio/gather", data={**params, "Digits": "1"}, headers={"X-Twilio-Signature": sig3})
    out["gather_press1_valid_sig"] = (r.status_code, r.text[:80])
    # status callback
    url3 = f"{BASE}/twilio/status"
    p4 = {"CallSid": "CAxxx", "CallStatus": "completed"}
    r = c.post("/twilio/status", data=p4, headers={"X-Twilio-Signature": twilio_signature(url3, p4)})
    out["status_valid_sig"] = (r.status_code, r.text[:60])
    r = c.post("/twilio/status", data=p4)
    out["status_no_sig"] = (r.status_code, r.text[:60])
    # GET dry run
    r = c.get("/twilio/twiml/test")
    out["twiml_test_no_cookie"] = (r.status_code, r.text[:40])
    rc = login()
    r = rc.get("/twilio/twiml/test")
    out["twiml_test_cookie"] = (r.status_code, r.text[:80])
    # beacon sensor
    body = {"beacon_id": "beacon1", "type": "heartbeat", "ts": "2026-11-12T10:00:00+08:00", "seq": 101,
            "battery": 0.9, "rssi": -60}
    r = c.post("/events/sensor", json=body)
    out["sensor_no_token"] = (r.status_code, r.text[:60])
    r = c.post("/events/sensor", json=body, headers={"X-Beacon-Token": "wrong"})
    out["sensor_wrong_token"] = (r.status_code, r.text[:60])
    r = c.post("/events/sensor", json=body, headers={"X-Beacon-Token": BEACON_TOKEN})
    out["sensor_ok_101"] = (r.status_code, r.text[:60])
    r = c.post("/events/sensor", json=body, headers={"X-Beacon-Token": BEACON_TOKEN})
    out["sensor_dup_101"] = (r.status_code, r.text[:60])
    # unknown beacon id with a syntactically valid token
    r = c.post("/events/sensor", json={**body, "beacon_id": "ghost"}, headers={"X-Beacon-Token": BEACON_TOKEN})
    out["sensor_unknown_beacon"] = (r.status_code, r.text[:60])
    # a real fall event via the beacon (dropped into the queue before reset below)
    fall = {"beacon_id": "beacon1", "type": "fall_suspected", "ts": "2026-11-12T10:01:00+08:00", "seq": 102,
            "peak_g": 3.1, "freefall_ms": 180, "stillness_s": 2.4}
    r = c.post("/events/sensor", json=fall, headers={"X-Beacon-Token": BEACON_TOKEN})
    out["sensor_fall_ok"] = (r.status_code, r.text[:60])
    await_st = login().get("/operator/api/state").json()
    out["incident_after_beacon_fall"] = (await_st.get("incident") or {}).get("state")
    login().post("/operator/api/override", json={"kind": "incident_resolve"})
    results["check5"] = out
    print("CHECK5 webhook auth:", json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    check4()
    check5()
    json.dump(results, open("/scratch/secrets_webhooks.json", "w"), indent=1, default=str)
    print("WROTE /scratch/secrets_webhooks.json")
