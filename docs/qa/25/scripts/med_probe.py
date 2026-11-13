"""QA 25 side probe: medicine answer after a Reset demo done before 08:00 (seed marks s_0800 taken at 08:05 "today").
Runs in the ahbo-api image on the qa25 network (stdlib only)."""
import http.cookiejar
import json
import sys
import time
import urllib.parse
import urllib.request

BASE = "http://ahbo-qa25-api:8000"
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def call(method, path, body=None, form=None):
    data, headers = None, {}
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with op.open(req, timeout=30) as r:
            txt = r.read().decode()
            try:
                return r.status, json.loads(txt)
            except Exception:
                return r.status, txt[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


print("login:", call("POST", "/login", form={"password": "qa-test-password"})[0])
for clock in sys.argv[1:] or ["07:00"]:
    print(f"\n=== clock {clock} → Reset demo → 「阿寶，我幾時食藥呀？」 ===")
    print("clock:", call("POST", "/operator/api/clock", {"set": clock}))
    print("reset:", call("POST", "/operator/api/reset"))
    print("wake:", call("POST", "/operator/api/trigger", {"kind": "wake"}))
    time.sleep(0.5)
    u = call("POST", "/operator/api/trigger", {"kind": "utterance", "text": "阿寶，我幾時食藥呀？"})
    print("utterance:", u)
    tid = (u[1] or {}).get("turn_id")
    time.sleep(4)
    tr = call("GET", "/operator/api/trace?limit=2000")[1]
    for e in tr.get("events", []):
        if e.get("type") in ("agent.reply_start", "agent.tool_result") and (e.get("payload") or {}).get("turn_id") == tid:
            pl = e.get("payload") or {}
            if e["type"] == "agent.tool_result":
                r = pl.get("result") or {}
                print("   get_med_status next:", json.dumps({k: (r.get("next") or {}).get(k) for k in ("slot_id", "time", "rel_words")}, ensure_ascii=False),
                      "| today:", json.dumps([(x.get("slot_id"), x.get("time"), x.get("state")) for x in (r.get("today") or r.get("slots") or [])], ensure_ascii=False))
            else:
                print("   reply:", pl.get("text"))
