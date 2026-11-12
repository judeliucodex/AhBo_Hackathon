"""HTTP real-path probe against the throwaway fake-mode server (no host ports)."""
import json
import re
import sys
import time
import urllib.request

BASE = "http://ahbo-qa22-api:8000"
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())

def go(path, data=None, hdrs=None):
    req = urllib.request.Request(BASE + path, data=data, headers=hdrs or {})
    with op.open(req, timeout=20) as r:
        return r.status, r.read().decode()

print("healthz:", go("/healthz")[1][:130])
body = "password=qa-test-password".encode()
print("login:", go("/login", body, {"Content-Type": "application/x-www-form-urlencoded"})[0])
print("scenario S2:", go("/operator/api/scenario/S2", b"{}", {"Content-Type": "application/json"})[0])
time.sleep(4)
st = json.loads(go("/operator/api/state")[1])
print("session:", st.get("session"))
print("incident:", st.get("incident"))
print("doses:", json.dumps(st.get("doses"), ensure_ascii=False))
print("ui cards:", json.dumps([c.get("label") for c in (st.get("ui") or {}).get("cards", [])], ensure_ascii=False))
print("spend:", json.dumps(st.get("spend"), ensure_ascii=False))
print("fake:", st.get("fake"))
# the S2 spoken line via the trace endpoint
tr = json.loads(go("/operator/api/trace?limit=40")[1])
lines = [e.get("payload", {}).get("text") for e in tr.get("events", []) if e.get("type") == "agent.reply_start"]
print("replies:", json.dumps(lines, ensure_ascii=False))
