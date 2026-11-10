"""Deep result JSON Schema (CONTRACTS §7, §24.8.3) and post-validation (code, not schema).

`DEEP_RESULT_SCHEMA` is exactly CONTRACTS §7. `validate_result(obj)` → list of errors. `finalize(obj, task_id)`
returns a result that is always safe to hand to the Kernel: schema-valid, `spoken_summary` passed through the spoken
filter (no digits/URLs/markdown/English, ≤ 2 sentences), URLs only in `sources` / `signup_methods`; anything that
cannot be repaired becomes `{status: "failed"}` with the fixed apology (§24.8.4). Web text is data (INV-7): it is
never turned into tool calls; it only reaches her as validated strings in this JSON.
"""
from __future__ import annotations

import copy
import re
from typing import Any

DEEP_RESULT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "additionalProperties": False,
    "required": ["status", "spoken_summary", "card", "sources", "signup_methods", "needs_confirmation"],
    "properties": {
        "task_id": {"type": "string"},
        "status": {"enum": ["done", "failed", "timeout"]},
        "spoken_summary": {"type": "string", "maxLength": 120},
        "card": {"type": "object", "additionalProperties": False, "required": ["title", "items"],
                 "properties": {"title": {"type": "string"},
                                "items": {"type": "array", "maxItems": 6, "items": {
                                    "type": "object", "additionalProperties": False, "required": ["time", "activity"],
                                    "properties": {"time": {"type": "string"}, "activity": {"type": "string"},
                                                   "place": {"type": "string"}, "why": {"type": "string"}}}}}},
        "sources": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                               "required": ["title", "url"],
                                               "properties": {"title": {"type": "string"}, "url": {"type": "string"}}}},
        "signup_methods": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["method"],
            "properties": {"method": {"enum": ["web_form", "phone", "in_person", "telegram_contact"]},
                           "url": {"type": "string"}, "phone": {"type": "string"}, "notes": {"type": "string"},
                           "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                           "activity_id": {"type": "string"}}}},
        "needs_confirmation": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["action", "args", "question"],
            "properties": {"action": {"enum": ["add_event", "signup"]}, "args": {"type": "object"},
                           "question": {"type": "string"}}}},
    },
}

APOLOGY = "唔好意思，我搵唔到，遲啲再試下好嗎？"
URL_RE = re.compile(r"(?i)(?:https?://|www\.)\S+")
_validator = None


def _get_validator():
    global _validator
    if _validator is None:
        from jsonschema import Draft202012Validator
        Draft202012Validator.check_schema(DEEP_RESULT_SCHEMA)
        _validator = Draft202012Validator(DEEP_RESULT_SCHEMA)
    return _validator


def validate_result(obj: Any) -> list[str]:
    """Schema errors (empty = valid)."""
    if not isinstance(obj, dict):
        return ["not an object"]
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
            for e in _get_validator().iter_errors(obj)]


def failed(task_id: str | None, status: str = "failed") -> dict[str, Any]:
    out: dict[str, Any] = {"status": status if status in ("failed", "timeout") else "failed",
                           "spoken_summary": APOLOGY, "card": {"title": "", "items": []}, "sources": [],
                           "signup_methods": [], "needs_confirmation": []}
    if task_id:
        out["task_id"] = task_id
    return out


def _strip_urls(s: Any) -> Any:
    return URL_RE.sub("", s).strip() if isinstance(s, str) else s


def post_validate(obj: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Code rules on a schema-valid result: speakable summary; URLs only in sources/signup_methods."""
    from ahbo.agent.filters.spoken import spoken_filter
    o = copy.deepcopy(obj)
    notes: list[str] = []
    fr = spoken_filter(o.get("spoken_summary", ""))
    if fr.text != o.get("spoken_summary"):
        notes.append(f"spoken_summary filtered ({','.join(fr.flags) or 'trimmed'})")
    o["spoken_summary"] = fr.text[:120]
    card = o.get("card") or {}
    if URL_RE.search(card.get("title", "")):
        card["title"] = _strip_urls(card["title"])
        notes.append("url removed from card title")
    for it in card.get("items", []):
        for k in ("time", "activity", "place", "why"):
            if k in it and URL_RE.search(it[k]):
                it[k] = _strip_urls(it[k])
                notes.append(f"url removed from card item {k}")
    for nc in o.get("needs_confirmation", []):
        if URL_RE.search(nc.get("question", "")):
            nc["question"] = _strip_urls(nc["question"])
            notes.append("url removed from a question")
        nc["question"] = spoken_filter(nc.get("question", "")).text
    if o.get("status") == "done" and not o["spoken_summary"]:
        notes.append("empty spoken_summary")
        return failed(o.get("task_id")), notes
    return o, notes


def finalize(obj: Any, task_id: str | None = None) -> dict[str, Any]:
    """Validate + post-validate. An invalid result never reaches the Kernel as-is: it becomes `failed`."""
    if isinstance(obj, dict) and task_id and "task_id" not in obj:
        obj = {**obj, "task_id": task_id}
    errors = validate_result(obj)
    if errors:
        return failed(task_id or (obj.get("task_id") if isinstance(obj, dict) else None))
    out, _ = post_validate(obj)
    if task_id:
        out["task_id"] = task_id
    return out


def parse_json_text(text: str) -> Any:
    """The model's final text → JSON (tolerates a ```json fence or prose around one object)."""
    import json
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S)
    if m:
        t = m.group(1)
    elif not t.startswith("{"):
        a, b = t.find("{"), t.rfind("}")
        t = t[a:b + 1] if a >= 0 and b > a else t
    return json.loads(t)
