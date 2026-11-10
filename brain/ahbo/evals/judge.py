"""Sonnet-as-judge (§16.2 "Judging the evals"): only where a deterministic check can't decide (tone, "answered the
question", "no medical advice" beyond the regexes), and only when `models` is not faked and a key exists. In fake
mode every judgement is recorded as "pending keys" with the rubric version, never as a pass.

Rubrics live in `evals/rubrics.yaml` (`version` + one text per rubric id).
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any

import yaml

from ahbo.core.settings import REPO_ROOT, Settings

log = logging.getLogger("ahbo.evals.judge")
RUBRICS_PATH = REPO_ROOT / "evals" / "rubrics.yaml"
PENDING = "pending keys"


@lru_cache(maxsize=1)
def rubrics() -> dict[str, Any]:
    if not RUBRICS_PATH.exists():
        return {"version": "missing", "rubrics": {}}
    return yaml.safe_load(RUBRICS_PATH.read_text(encoding="utf-8"))


def available(settings: Settings) -> bool:
    return not settings.is_fake("models") and bool(settings.anthropic_api_key)


async def judge(rubric_id: str, *, prompt_lines: list[str], reply_lines: list[str], settings: Settings,
                model_cfg: dict[str, Any]) -> dict[str, Any]:
    r = rubrics()
    version = str(r.get("version"))
    text = (r.get("rubrics") or {}).get(rubric_id)
    if text is None:
        return {"status": "error", "rubric": rubric_id, "rubric_version": version, "detail": "unknown rubric"}
    if not available(settings):
        return {"status": PENDING, "rubric": rubric_id, "rubric_version": version}
    from anthropic import AsyncAnthropic   # anthropic==2.7.0: AsyncAnthropic().messages.create(...)
    sonnet = model_cfg.get("sonnet") or {}
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    content = (f"評分準則（版本 {version}）：\n{text}\n\n"
               f"王婆婆講：\n" + "\n".join(prompt_lines) + "\n\n阿寶答：\n" + "\n".join(reply_lines) +
               '\n\n只回覆 JSON：{"pass": true|false, "reason": "<一句>"}')
    try:
        msg = await client.messages.create(model=sonnet.get("model", "claude-sonnet-5-5"), max_tokens=300,
                                           messages=[{"role": "user", "content": content}])
        out = "".join(getattr(b, "text", "") for b in msg.content)
        m = re.search(r"\{.*\}", out, re.S)
        verdict = json.loads(m.group(0)) if m else {"pass": False, "reason": "unparseable"}
        return {"status": "pass" if verdict.get("pass") else "fail", "rubric": rubric_id,
                "rubric_version": version, "reason": verdict.get("reason", "")}
    except Exception as e:   # a judge failure is reported, never counted as a pass
        log.warning("judge call failed: %s", type(e).__name__)
        return {"status": "error", "rubric": rubric_id, "rubric_version": version, "detail": type(e).__name__}
