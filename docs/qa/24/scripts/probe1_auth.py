"""QA24 probe 1: auth matrix over CONTRACTS §8–9/§14 routes + cookie flags + WS auth combos."""
import asyncio, json, re, sqlite3, sys, httpx

BASE = "http://ahbo-qa24-api:8000"
PASSWORD = "qa-test-password"
ENV = {}
for line in open("/scratch/env.txt"):
    k, _, v = line.strip().partition("=")
    ENV[k] = v
APP_SECRET = ENV["APP_SECRET"]
INTERNAL_TOKEN = ENV["INTERNAL_TOKEN"]
results = []


def rec(route, auth, code, note="", ok=None):
    results.append({"route": route, "auth": auth, "code": code, "note": note})
    print(f"{route:52s} {auth:16s} -> {code} {note}")


def wrong_cookie():
    return {"ahbo_session": "forged.foregedsignature"}


def get_token_via_ws():
    """Open the device channel with Bearer APP_SECRET, send hello, return (device_token, welcome)."""
    import websockets

    async def run():
        hdrs = {"Authorization": f"Bearer {APP_SECRET}"}
        async with websockets.connect("ws://ahbo-qa24-api:8000/ws/device", additional_headers=hdrs) as ws:
            await ws.send(json.dumps({"id": "01JAAAATESTHELLO000000", "type": "hello",
                                      "device_id": "ipad-matrix-1", "app_version": "0.1.0",
                                      "fixed_cache_version": "", "wake_engine": "speech_transcriber"}))
            while True:
                w = json.loads(await asyncio.wait_for(ws.recv(), 5))
                if w.get("type") == "welcome":
                    # ack nothing; just close
                    return w
    return asyncio.run(run())


def jpeg_bytes(n=64):
    return (b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * n)


def db_rows(sql):
    out = subprocess_sql(sql)
    return out


def subprocess_sql(sql):
    import subprocess
    r = subprocess.run(["python", "-c", f"""
import sqlite3, json
con = sqlite3.connect('/tmp/qa/ahbo.sqlite')
con.row_factory = sqlite3.Row
print(json.dumps([dict(x) for x in con.execute({sql!r}).fetchall()]))
"""], capture_output=True, text=True)
    return json.loads(r.stdout.strip() or "[]")


def main():
    # ANON: never logs in (no cookie jar pollution). AUTH: logs in once; jar carries the session cookie.
    c = httpx.Client(base_url=BASE, timeout=10.0, follow_redirects=False)   # anonymous requests only
    auth = httpx.Client(base_url=BASE, timeout=10.0, follow_redirects=False)  # logged-in requests only
    dev = httpx.Client(base_url=BASE, timeout=10.0, follow_redirects=False)   # device-token requests only

    # ---- login + cookie flags -------------------------------------------------
    r = auth.post("/login", data={"password": "wrong-password"})
    rec("POST /login", "wrong password", r.status_code, "expect 401")
    r = auth.post("/login", data={"password": PASSWORD})
    setc = r.headers.get("set-cookie", "")
    flags = {f.split("=")[0].strip().lower() for f in setc.split(";")[1:]}
    rec("POST /login", "correct password", r.status_code,
        f"set-cookie flags={sorted(flags)} maxage={'max-age=43200' in setc.lower()}")
    cookie = re.match(r"ahbo_session=([^;]+)", setc).group(1)
    OPER = {"Cookie": f"ahbo_session={cookie}"}

    # ---- device token (for device-token routes) -------------------------------
    w = get_token_via_ws()
    dt = w.get("device_token", "")
    print(f"welcome keys={sorted(w.keys())} device_token_prefix={dt[:6]}.…")
    DEV = {"Authorization": f"Bearer {dt}"}

    # ---- helper for expected-auth tuples --------------------------------------
    def matrix(route, method="GET", *, public=None, cookie=None, device=None, **kw):
        """public/cookie/device = expected code when unauthenticated / with operator cookie / with device token."""
        for label, hdrs, want, client in (("no auth", {}, public, c), ("wrong cookie", wrong_cookie(), public, c),
                                          ("device token", DEV, device, dev), ("operator cookie", OPER, cookie, auth)):
            r = client.request(method, route, headers=hdrs, **kw)
            note = f"expect {want}"
            status = r.status_code
            rec(route, label, status, note)

    # ---- §8 public ----
    matrix("/healthz", public=200, cookie=200, device=200)
    matrix("/login", public=200, cookie=303)          # with cookie redirects to next
    matrix("/face/index.html", public=200, cookie=200, device=200)
    matrix("/mock-centre/", public=200, cookie=200, device=200)
    matrix("/mock-centre/api/activities", public=200, cookie=200, device=200)
    r = c.post("/mock-centre/api/register", json={})
    rec("POST /mock-centre/api/register", "no auth, empty body", r.status_code, "public; 4xx-validation ok")

    # ---- §8/§14 cookie pages ----
    for route in ("/", "/sim/", "/sim/index.html", "/operator/", "/operator/index.html"):
        matrix(route, public=303, cookie=None)        # operator-ui deleted: with cookie may be 404
    r = c.get("/", headers=OPER); rec("GET / (with cookie)", "operator cookie", r.status_code, "→ /sim/")
    r = c.get("/sim/", headers=OPER); rec("GET /sim/ (with cookie)", "operator cookie", r.status_code)
    r = c.get("/operator/", headers=OPER); rec("GET /operator/ (with cookie)", "operator cookie", r.status_code,
                                               "operator-ui deleted → 404 acceptable")
    r = c.post("/logout"); rec("POST /logout", "no auth", r.status_code, "public → 303 + clears cookie")
    rec("POST /logout", "set-cookie", r.headers.get("set-cookie", "")[:60])

    # ---- §9 operator API ----
    for route in ("/operator/api/state", "/operator/api/health", "/operator/api/outbox",
                  "/operator/api/trace", "/operator/api/fixed"):
        matrix(route, public=401, cookie=200, device=401)
    matrix("/operator/api/trigger", "POST", public=401, cookie=200, device=401, json={"kind": "wake"})
    matrix("/operator/api/clock", "POST", public=401, cookie=200, device=401, json={"offset_s": 0})
    matrix("/operator/api/outbox/x/press", "POST", public=401, cookie=None, device=401, json={"button": "handled"})
    r = c.get("/operator/stream"); rec("GET /operator/stream", "no auth", r.status_code, "expect 401")
    with c.stream("GET", "/operator/stream", headers=OPER) as r:
        rec("GET /operator/stream", "operator cookie", r.status_code, "SSE open")

    # ---- §8 device-token routes ----
    matrix("/session", "POST", public=401, cookie=200, device=200, json={"reason": "touch", "mode": "companion"})
    r = c.post("/session", json={"reason": "touch"}, headers={"Authorization": f"Bearer {APP_SECRET}"})
    rec("POST /session", "Bearer APP_SECRET", r.status_code, "expect 401 (APP_SECRET is not a device token)")
    r = c.post("/session", json={"reason": "touch"}, headers={"Authorization": "Bearer dt.fake.1.bad"})
    rec("POST /session", "Bearer forged dt token", r.status_code, "expect 401")

    # media/photo upload (valid jpeg)
    files = {"file": ("p.jpg", jpeg_bytes(), "image/jpeg")}
    r = c.post("/media/photo", files=files, data={"incident_id": "inc_test"}, headers=DEV)
    photo_id = ""
    if r.status_code == 200:
        photo_id = r.json().get("photo_id", "")
    rec("POST /media/photo", "device token", r.status_code, f"photo_id={photo_id}")
    if photo_id:
        matrix(f"/media/photo/{photo_id}", public=401, cookie=200, device=403)  # fall photo: console-only

    manifest = c.get("/media/fixed/manifest.json", headers=DEV)
    rec("GET /media/fixed/manifest.json", "device token", manifest.status_code)
    clips = list((manifest.json().get("clips") or {}).keys())
    clip0 = clips[0] if clips else "F1"
    fname = (manifest.json().get("clips", {}).get(clip0) or {}).get("file", "F1.m4a")
    matrix(f"/media/fixed/{fname}", public=401, cookie=200, device=200)
    matrix("/media/fixed/manifest.json", public=401)

    ids = json.load(open("/scratch/ids.json"))
    vid, rand = ids["voice_id"], ids["public_rand"]
    matrix(f"/media/voice/{vid}", public=401, cookie=200, device=200)

    matrix("/media/screens/whatever.png", public=401, cookie=404, device=401)   # console-only; file may not exist

    # public clip: unguessable path only
    matrix(f"/media/public/{rand}/{fname}", public=200, cookie=200, device=200)
    matrix("/media/public/short/P1.m4a", public=404, cookie=404, device=404)    # rand too short

    # ---- §14 internal ----
    INT = {"Authorization": f"Bearer {INTERNAL_TOKEN}"}
    matrix("/internal/context/sess_x", public=401, cookie=401, device=401)
    r = c.get("/internal/context/sess_x", headers={"Authorization": "Bearer wrong"})
    rec("GET /internal/context/sess_x", "wrong internal token", r.status_code, "expect 401")
    r = c.get("/internal/context/sess_x", headers=INT)
    rec("GET /internal/context/sess_x", "internal token", r.status_code, "200/501 = auth ok")
    r = c.post("/internal/turn", json={"op": "user", "session_id": "s", "turn_id": "t", "text": "你好"}, headers=INT)
    rec("POST /internal/turn", "internal token", r.status_code)
    r = c.post("/internal/event", json={"type": "quality.turn_latency", "payload": {"total_ms": 5}}, headers=INT)
    rec("POST /internal/event", "internal token", r.status_code)
    r = c.post("/internal/clock", json={"offset_s": 0}, headers=INT)
    rec("POST /internal/clock", "internal token", r.status_code)

    json.dump(results, open("/scratch/auth_matrix.json", "w"), indent=1)
    print("\nWROTE /scratch/auth_matrix.json:", len(results), "rows")


if __name__ == "__main__":
    main()
