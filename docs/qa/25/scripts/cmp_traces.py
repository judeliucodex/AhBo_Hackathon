"""Print the kit storyboard trace and the sim trace compactly, side by side per scenario (heard partials dropped)."""
import json
import sys

KIT = "/opt/ahbo/brain/tests/golden/kit"
SIM = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ahbo-qa-25/out/check1"
ONLY = sys.argv[2:] or ["S1", "S2", "S3", "S4", "S4ok"]


def compact(cmd):
    t = cmd["type"]
    if t == "mood":
        return f"mood {cmd['mood']}"
    if t == "heard":
        return f"heard{' FINAL' if cmd.get('final') else ''} 「{cmd.get('text')}」"
    if t == "say_start":
        ex = {k: v for k, v in cmd.items() if k not in ("type", "id", "text", "segments", "durationSec")}
        seg = f" segs={len(cmd['segments'])}" if cmd.get("segments") else ""
        dur = f" dur={cmd['durationSec']:.2f}" if isinstance(cmd.get("durationSec"), (int, float)) else ""
        return f"say_start 「{cmd.get('text')}」{seg}{dur} {ex if ex else ''}"
    if t == "say_end":
        return "say_end"
    if t == "card":
        c = dict(cmd["card"])
        return "card " + json.dumps(c, ensure_ascii=False)
    if t == "card_update":
        return f"card_update {cmd.get('id')} {json.dumps(cmd.get('patch'), ensure_ascii=False)}"
    if t == "card_clear":
        return f"card_clear {cmd.get('id') or '(all)'}"
    if t == "steps":
        g = cmd["group"]
        return "steps " + json.dumps({"title": g.get("title"), "steps": [[s["id"], s["label"], s.get("doneLabel"), s.get("state")] for s in g["steps"]]}, ensure_ascii=False)
    if t == "step":
        return f"step {cmd.get('id')} → {cmd.get('state')}" + (f" label=「{cmd['label']}」" if cmd.get("label") else "")
    if t == "steps_clear":
        return f"steps_clear {cmd.get('group') or '(all)'}"
    if t == "ok_button":
        return f"ok_button show={cmd.get('show')}" + (f" label={cmd['label']}" if cmd.get("label") else "")
    if t == "countdown":
        return f"countdown n={cmd.get('n')}"
    return t + " " + json.dumps({k: v for k, v in cmd.items() if k != "type"}, ensure_ascii=False)


def load(path):
    tr = json.load(open(path))
    out = []
    for e in tr:
        cmd = e["cmd"]
        if cmd["type"] == "heard" and not cmd.get("final"):
            continue
        if cmd["type"] == "level":
            continue
        out.append((e["t_ms"], compact(cmd)))
    return out


for s in ONLY:
    print(f"\n================ {s} ================")
    print("---- KIT (face/dev.html storyboard) ----")
    for t, line in load(f"{KIT}/{s}.json"):
        print(f"{t:6d}  {line}")
    print("---- SIM (what /sim/ forwarded to the face) ----")
    for t, line in load(f"{SIM}/{s}.trace.json"):
        print(f"{t:6d}  {line}")
