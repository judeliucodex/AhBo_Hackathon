"""Golden UI trace normalisation (CONTRACTS §13)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

KIT = Path(__file__).parent / "golden" / "kit"
DROP = {"heard", "level", "expr", "reset", "say_update", "birth", "offline"}
DEFAULT_ICON = {"med": "", "event": "🏥", "plan": "🚌", "voice_note": "🎙", "incident": "⚠️", "action": "📞",
                "message": "💬", "story": "📖", "today": "☀️", "photo": "📷", "activity": "🎤", "info": "ℹ️"}


# Owner decisions that change kit labels (behaviour wins over the kit's text; kinds and order still compared).
# 11 Nov: fall calls go to the caregiver 阿珍, not an emergency centre; in S4-OK the camera shows her fine, so the
# photo step shows the default 「相片已送出」 (a concern-raising note would now trigger a call).
OWNER_LABELS: dict[str, list[tuple[str, str]]] = {
    "S4": [('"已致電緊急中心"', '"已致電阿珍"'), ('"致電緊急中心"', '"致電阿珍"'), ('"緊急中心"', '"致電阿珍"')],
    "S4ok": [('"已致電緊急中心"', '"已致電阿珍"'), ('"致電緊急中心"', '"致電阿珍"'),
             ('"doneLabel": "可能有人跌咗喺地下"', '"doneLabel": "相片已送出"')],
}


def kit_trace(name: str) -> list[dict[str, Any]]:
    raw = json.dumps([r["cmd"] for r in json.loads((KIT / f"{name}.json").read_text())], ensure_ascii=False)
    for a, b in OWNER_LABELS.get(name, []):
        raw = raw.replace(a, b)
    return json.loads(raw)


def from_device(sent: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Device-hub commands → face commands (play_fixed / play_voice_message become a say pair)."""
    out = []
    for x in sent:
        if x["type"] == "device.ui":
            out.append(x["ui"])
        elif x["type"] == "device.play_fixed":
            out += [{"type": "say_start", "id": x["say_id"], "from": "ahbo"}, {"type": "say_end", "id": x["say_id"]}]
        elif x["type"] == "device.play_voice_message":
            out += [{"type": "say_start", "id": x["say_id"], "from": "family", "fromName": x.get("from_name")},
                    {"type": "say_end", "id": x["say_id"]}]
    return out


def normalise(cmds: list[dict[str, Any]]) -> list[tuple]:
    groups: dict[str, dict[str, Any]] = {}
    out: list[tuple] = []
    for c in cmds:
        t = c.get("type")
        if t in DROP:
            continue
        if t == "mood":
            out.append(("mood", c["mood"]))
        elif t == "say_start":
            out.append(("say", c.get("from") or "ahbo"))
        elif t == "say_end":
            out.append(("say_end",))
        elif t == "card":
            k = c["card"]
            out.append(("card", k.get("kind"), k.get("label"), k.get("small"), k.get("icon") or DEFAULT_ICON.get(k.get("kind"), ""),
                        bool(k.get("persistent")), tuple(k.get("pill") or ())))
        elif t == "card_update":
            out.append(("card_update", tuple(sorted((c.get("patch") or {}).items()))))
        elif t == "card_clear":
            out.append(("card_clear", "id" if c.get("id") else "all"))
        elif t == "steps":
            g = c["group"]
            gi = len(groups)
            groups[g["id"]] = {"i": gi, "steps": {s["id"]: {"i": j, "label": s["label"], "done": s.get("doneLabel")}
                                                 for j, s in enumerate(g["steps"])}}
            out.append(("steps", g.get("title"), tuple((s["label"], s.get("state", "pending")) for s in g["steps"])))
        elif t == "step":
            g = groups.get(c["group"])
            if g is None:
                out.append(("step?", c["state"]))
                continue
            s = g["steps"].setdefault(c["id"], {"i": len(g["steps"]), "label": c.get("label", ""), "done": None})
            label = c.get("label") or (s["done"] if c["state"] == "done" and s["done"] else s["label"])
            out.append(("step", g["i"], s["i"], c["state"], label))
        elif t == "steps_clear":
            out.append(("steps_clear",))   # with or without a group id: same face result when one group is open
        elif t == "ok_button":
            out.append(("ok_button", bool(c.get("show"))))
        elif t == "countdown":
            out.append(("countdown", c.get("n")))
        else:
            out.append((t,))
    return out


def compare(kit: list[dict[str, Any]], ours: list[dict[str, Any]]) -> tuple[bool, str]:
    a, b = normalise(kit), normalise(ours)
    if b[: len(a)] == a:
        return True, f"match; {len(b) - len(a)} extra trailing: {b[len(a):]}"
    lines = []
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else None
        y = b[i] if i < len(b) else None
        lines.append(f"{'  ' if x == y else '!!'} {i:2d} kit={x}\n      ours={y}")
    return False, "\n".join(lines)
