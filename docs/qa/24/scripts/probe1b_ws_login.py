"""QA24 probe 1b: WS auth combos (4401), public clip path, login rate limit (5/min/IP), cookie Secure note."""
import asyncio, json, httpx, websockets

BASE = "http://ahbo-qa24-api:8000"
WS = "ws://ahbo-qa24-api:8000/ws/device"
PASSWORD = "qa-test-password"
ENV = dict(l.split("=", 1) for l in open("/scratch/env.txt").read().strip().splitlines())
APP_SECRET = ENV["APP_SECRET"]
out = []


def rec(what, result, note=""):
    out.append({"what": what, "result": result, "note": note})
    print(f"{what:58s} -> {result} {note}")


async def ws_connect(headers=None, sim_param=None):
    url = WS + (f"?sim={sim_param}" if sim_param else "")
    try:
        async with websockets.connect(url, additional_headers=headers or {}, open_timeout=5) as ws:
            try:
                first = await asyncio.wait_for(ws.recv(), 1.5)
                return f"OPEN first_frame={json.loads(first).get('type')}"
            except TimeoutError:
                return "OPEN (no frame before hello - correct)"   # server waits for hello
            except websockets.exceptions.ConnectionClosed as e:
                return f"CLOSED code={e.rcvd.code if e.rcvd else '?'}"
    except websockets.exceptions.InvalidStatus as e:
        return f"HTTP_{e.response.status_code}"
    except websockets.exceptions.ConnectionClosed as e:
        return f"CLOSED code={e.rcvd.code if e.rcvd else '?'}"


async def ws_main():
    rec("WS /ws/device no auth", await ws_connect(), "expect close 4401")
    rec("WS wrong bearer", await ws_connect({"Authorization": "Bearer wrong-secret"}), "expect close 4401")
    rec("WS bearer = device token (dt.)", await ws_connect({"Authorization": "Bearer dt.x.1.y"}), "expect close 4401")
    rec("WS cookie valid, no ?sim=1", await ws_connect({"Cookie": f"ahbo_session={COOKIE}"}), "expect close 4401")
    rec("WS cookie valid + sim=1", await ws_connect({"Cookie": f"ahbo_session={COOKIE}"}, "1"),
        "expect OPEN (browser iPad)")
    rec("WS cookie valid + sim=0", await ws_connect({"Cookie": f"ahbo_session={COOKIE}"}, "0"), "expect close 4401")
    rec("WS cookie forged + sim=1", await ws_connect({"Cookie": "ahbo_session=forged.sig"}, "1"), "expect close 4401")
    rec("WS bearer APP_SECRET", await ws_connect({"Authorization": f"Bearer {APP_SECRET}"}), "expect OPEN")


def public_clip():
    rand = json.load(open("/scratch/ids.json"))["public_rand"]
    c = httpx.Client(base_url=BASE, timeout=10, follow_redirects=False)
    r = c.get(f"/media/public/{rand}/P1.m4a")
    rec(f"GET /media/public/{rand}/P1.m4a (no auth)", r.status_code, "unguessable path: expect 200")
    r = c.get(f"/media/public/{rand}/../../ahbo.sqlite")
    rec("GET /media/public/…/../../ahbo.sqlite", r.status_code, "expect 404")
    r = c.get(f"/media/public/{rand}/nope.m4a")
    rec(f"GET /media/public/{rand}/nope.m4a", r.status_code, "expect 404")


def rate_limit():
    c = httpx.Client(base_url=BASE, timeout=10, follow_redirects=False)
    codes = []
    for i in range(8):
        r = c.post("/login", data={"password": "definitely-wrong"})
        codes.append(r.status_code)
    rec("POST /login x8 wrong password", codes, "expect [401 x5, 429, 429, 429]")
    r = c.post("/login", data={"password": PASSWORD})
    rec("POST /login correct after limit", r.status_code, "expect 429 (limit counts all attempts)")
    # second IP (X-Forwarded-For) — on the bare container this IS trusted; behind Caddy it is overwritten
    c2 = httpx.Client(base_url=BASE, timeout=10, follow_redirects=False)
    r = c2.post("/login", data={"password": "x"}, headers={"X-Forwarded-For": "9.9.9.9"})
    rec("POST /login with X-Forwarded-For 9.9.9.9", r.status_code, "401 = different key (app trusts last XFF)")


if __name__ == "__main__":
    # login on its own client to get the cookie
    lc = httpx.Client(base_url=BASE, timeout=10, follow_redirects=False)
    r = lc.post("/login", data={"password": PASSWORD, "next": "/sim/"})
    COOKIE = r.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    globals()["COOKIE"] = COOKIE
    asyncio.run(ws_main())
    public_clip()
    rate_limit()
    json.dump(out, open("/scratch/ws_login_public.json", "w"), indent=1)
    print("\nWROTE /scratch/ws_login_public.json")
