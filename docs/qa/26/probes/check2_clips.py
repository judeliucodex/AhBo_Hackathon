"""QA26 check 2: fixed-clip manifest vs CONTRACTS §11 / PRD §10.3 (fake speech)."""
import hashlib
import json
import subprocess
import sys

sys.path.insert(0, "/repo/brain")

from ahbo.core.db import open_db
from ahbo.core.jsonutil import dumps
from ahbo.core.settings import get_settings
from ahbo.speech.segments import u16len

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


st = get_settings()
db = open_db("/tmp/qa/ahbo.sqlite")
m = json.load(open("/tmp/qa/media/fixed/manifest.json"))

# ---- 1. fixed ids + texts match fixed_lines.yaml
from ahbo.core.config import load_config
cfg = load_config(fresh=True)
fixed_ids = ["F1", "F2", "F3", "F4", "F4p", "F5", "A1", "G1", "G2"]
for cid in fixed_ids:
    c = m["clips"].get(cid)
    check(f"clip {cid} present", c is not None)
    if c:
        check(f"clip {cid} text == fixed_lines.yaml", c["text"] == cfg.fixed_lines[cid],
              repr(c["text"][:30]))

# ---- 2. R_<slot> per active dose slot, med names joined like the agent says them
from ahbo.models.fakes import join_names
cat = {r["id"]: r["yue"] for r in db.query("SELECT id, yue FROM med_catalogue")}
slots = db.query("SELECT slot_id, med_names FROM med_schedule WHERE active=1 ORDER BY time")
for row in slots:
    ids = json.loads(row["med_names"])
    names = [cat[i] for i in ids if i in cat]
    want = cfg.fixed_lines["R_TEMPLATE"].format(med_names=join_names(names))
    got = m["clips"].get(f"R_{row['slot_id']}")
    check(f"R_{row['slot_id']} present", got is not None)
    if got:
        check(f"R_{row['slot_id']} text == template with joined names", got["text"] == want, repr(got["text"]))
rs = [k for k in m["clips"] if k.startswith("R_")]
check("no extra R_ clips", sorted(rs) == sorted(f"R_{r['slot_id']}" for r in slots), f"{sorted(rs)}")

# ---- 3. P1 + public section under a random public path
p1 = m["clips"].get("P1")
check("clips.P1 present", p1 is not None)
addr = db.one("SELECT address FROM resident WHERE id='resident'")["address"]
want_p1 = cfg.fixed_lines["P1_TEMPLATE"].format(address=addr)
check("P1 text == P1_TEMPLATE with address", p1 and p1["text"] == want_p1, repr((p1 or {}).get("text", "")[:40]))
pub = m.get("public")
check("manifest has public section", isinstance(pub, dict) and pub.get("dir"))
rand = db.kv_get("public_clip_rand")
path = db.kv_get("public_clip_path")
check("kv public_clip_rand == public.dir", rand == (pub or {}).get("dir"), f"{rand}")
check("kv public_clip_path == /media/public/<rand>/P1.m4a", path == f"/media/public/{rand}/P1.m4a", f"{path}")
import os
check("public file exists on disk", os.path.isfile(f"/tmp/qa/media/public/{rand}/P1.m4a"))
check("public dir is a lowercase ULID (unguessable)", len(rand) == 26 and rand.islower() and rand.isalnum(), rand)

# ---- 4. durations match ffprobe (±0.1 s)
for cid, c in sorted(m["clips"].items()):
    f = f"/tmp/qa/media/fixed/{c['file']}"
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", f], capture_output=True, text=True)
    try:
        real = float(out.stdout.strip())
    except ValueError:
        check(f"{cid} ffprobe", False, out.stderr[:80])
        continue
    check(f"{cid} duration ffprobe ±0.1s", abs(real - c["duration"]) <= 0.1,
          f"manifest {c['duration']} vs ffprobe {real:.3f}")

# ---- 5. segments: monotonic, non-overlapping, UTF-16, end == text length, within duration
for cid, c in sorted(m["clips"].items()):
    segs, text, dur = c["segments"], c["text"], c["duration"]
    n = u16len(text)
    ok = segs and segs[0][0] == 0 and segs[-1][1] == n
    mono = all(b > a for a, b in [(s[0], s[1]) for s in segs]) and \
        all(segs[i][1] <= segs[i + 1][0] for i in range(len(segs) - 1)) and \
        all(segs[i][3] <= segs[i + 1][2] + 1e-9 for i in range(len(segs) - 1))
    inrange = all(0 <= s[2] <= s[3] <= dur + 0.001 and 0 <= s[0] < s[1] <= n for s in segs)
    check(f"{cid} segments cover text (0..{n}) in order", ok and mono and inrange,
          f"{len(segs)} segs, first={segs[0] if segs else None}, last={segs[-1] if segs else None}")
    # gap coverage: every char offset is revealed by some segment
    covered = set()
    for s in segs:
        covered.update(range(s[0], s[1]))
    check(f"{cid} every UTF-16 offset revealed", covered == set(range(n)), f"missing {sorted(set(range(n)) - covered)[:5]}")

# ---- 6. F4/F4p = owner's wording (BUILD_LOG §8)
check("F4 says 打電話俾阿珍", "我已經打電話俾阿珍" in m["clips"]["F4"]["text"], m["clips"]["F4"]["text"])
check("F4p says 打電話俾阿珍", "我已經打電話俾阿珍" in m["clips"]["F4p"]["text"], m["clips"]["F4p"]["text"])
check("F4 makes no ambulance claim", "救護車" not in m["clips"]["F4"]["text"] and "ambulance" not in m["clips"]["F4"]["text"].lower())

# ---- 7. version = f-<sha1(texts+voice+mode)>
texts = {k: v["text"] for k, v in m["clips"].items()}          # plan_texts() includes P1
h = hashlib.sha1(dumps({"texts": texts, "voice": st.azure_tts_voice, "mode": "fake"}).encode()).hexdigest()[:12]
check("version = f-sha1(texts+voice+fake)", m["version"] == f"f-{h}", m["version"])
check("no clip is the platform fallback", not m.get("fallback"))

print(f"\n{'ALL PASS' if not FAILS else 'FAILURES: ' + '; '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
