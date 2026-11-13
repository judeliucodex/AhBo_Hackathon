"""QA 25 side probe 2: after a Reset demo at 07:00 (seed: s_0800 taken at 08:05), does the 08:00 reminder still fire?
Control: the 12:00 slot (scheduled) with the clock moved to 11:59."""
import http.cookiejar
import json
import time
import urllib.parse
import urllib.request

BASE = "http://ahbo-qa25-api:8000"
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


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


def watch(prefix_a, prefix_b, secs):
    import re
    seen = {}
    end = time.time() + secs
    while time.time() < end:
        tr = call("GET", "/operator/api/trace?limit=2000")[1]
        for kind in ("events", "commands"):
            for e in tr.get(kind, []):
                ts = str(e.get("ts") or "")
                ts_ok = re.match(r"2026-11-12[T ]0[78]:5[89]|2026-11-12[T ]08:0|2026-11-12[T ]12:0", ts)
                if ts_ok:
                    seen[e["id"]] = (ts[11:19], kind[:3], e["type"], json.dumps(e.get("payload"), ensure_ascii=False)[:150])
        time.sleep(3)
    for v in sorted(seen.values()):
        print("   ", *v)
    if not seen:
        print("    (no dose.* / play_fixed / session.open / family.send events in that window)")


print("login:", call("POST", "/login", form={"password": "qa-test-password"})[0])
print("\n=== A. Reset at 07:00, then clock → 07:59, watch 50 s across 08:00 ===")
print(call("POST", "/operator/api/clock", {"set": "07:00"})[1])
print(call("POST", "/operator/api/reset")[1])
print(call("POST", "/operator/api/clock", {"set": "07:59"})[1])
watch("", "", 80)
print("\n=== B. control: clock → 11:59, watch 50 s across 12:00 (slot s_1200 is 'scheduled') ===")
print(call("POST", "/operator/api/clock", {"set": "11:59"})[1])
watch("", "", 80)
print("reset at the end:", call("POST", "/operator/api/reset")[1])
