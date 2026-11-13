"""QA 25 check 3b: sign-up against the mock centre's blocked variants, through the agent's own tool path
(POST /internal/tool/request_action + confirm_action). Runs inside the throwaway api container.
Usage: python /scratch/variant_probe.py hkid captcha ...   ("plain" = no variant; "evil" = a non-allowlisted URL)"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, "/repo/brain")
from ahbo.core.db import open_db

BASE = "http://localhost:8000"
PUB = os.environ["PUBLIC_BASE_URL"]
TOK = os.environ["INTERNAL_TOKEN"]


def tool(name, args):
    req = urllib.request.Request(f"{BASE}/internal/tool/{name}", data=json.dumps({"args": args}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOK}"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"http": e.code, "body": e.read().decode()[:300]}


db = open_db("/tmp/qa/ahbo.sqlite")
for v in [a for a in sys.argv[1:] if not a.startswith("--")] or ["hkid", "captcha"]:
    url = {"plain": f"{PUB}/mock-centre/", "evil": "http://example.com/signup"}.get(v, f"{PUB}/mock-centre/?variant={v}")
    print(f"\n=== {v}: {url} ===")
    r = tool("request_action", {"kind": "signup", "target": "act_opera_1500",
                                "details": {"activity_id": "act_opera_1500", "title": "粵曲班", "time": "15:00", "url": url}})
    print("request_action:", json.dumps(r, ensure_ascii=False)[:300])
    aid = r.get("action_id")
    if not aid:
        continue
    row = db.one("SELECT details FROM actions WHERE id=?", (aid,))
    d = json.loads(row["details"])
    print("stored details.url:", d.get("url"))
    if "--pin" in sys.argv and d.get("url") != url:
        d["url"] = url
        db.execute("UPDATE actions SET details=? WHERE id=?", (json.dumps(d, ensure_ascii=False), aid))
        print("pinned details.url in my throwaway DB (same technique as brain/tests/actions/test_playwright_integration.py)")
    r2 = tool("confirm_action", {"action_id": aid, "utterance": "好呀", "stt_confidence": 0.95})
    print("confirm_action:", json.dumps(r2, ensure_ascii=False)[:300])
    t0 = time.time()
    st = None
    while time.time() - t0 < 130:
        st = db.one("SELECT state,result,screenshots FROM actions WHERE id=?", (aid,))
        if st["state"] in ("done", "needs_human", "failed"):
            break
        time.sleep(0.5)
    print(f"final state after {time.time() - t0:.1f}s:", st["state"])
    print("result:", (st["result"] or "")[:600])
    print("screenshots:", st["screenshots"])
    time.sleep(1.5)
    for o in db.query("SELECT channel,kind,to_contact,payload,buttons FROM outbox WHERE correlation_id=? ORDER BY ts", (aid,)):
        pl = json.loads(o["payload"] or "{}")
        print("outbox:", o["channel"], o["kind"], o["to_contact"], "|", json.dumps(pl.get("text"), ensure_ascii=False)[:500],
              "| screenshots:", pl.get("screenshots"), "| buttons:", o["buttons"][:120])
print("\nmock_registrations:", [dict(x) for x in db.query("SELECT reference,name_zh,activity_id FROM mock_registrations")])
