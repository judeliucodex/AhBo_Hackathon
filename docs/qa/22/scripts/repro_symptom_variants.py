"""Repro for the red-flag variant gap: chest-pain phrasings that the deterministic matcher and the fake agent
both miss. Output → /scratch/symptom_variants.json (evidence for the report)."""
import asyncio
import json
import os
import sys

os.environ["AHBO_FAKE"] = "all"
os.environ["AHBO_PROFILE"] = "demo"

from ahbo.evals.harness import EvalSystem  # noqa: E402
from ahbo.kernel.matchers import match_redflag  # noqa: E402

PHRASES = [
    "阿寶，我胸口痛呀",              # exact redflags.yaml phrase
    "阿寶，我胸口好痛呀",            # natural variant with 好
    "心口好痛呀，阿寶",              # 心口 variant
    "我唞唔到氣呀",                  # exact
    "我透大氣都透唔到呀",            # variant
]


async def one(phrase: str) -> dict:
    s = await EvalSystem.create()
    try:
        s.clock.set_time("10:00")
        s.kernel.load()
        await s.say(phrase)
        await s.wait(1.5)
        await s.settle()
        concerns = [json.loads(r["payload"]) for r in
                    s.db.query("SELECT payload FROM events WHERE type='agent.concern'")]
        inc = s.kernel.state.incident
        return {"phrase": phrase,
                "matcher": match_redflag(phrase),
                "concern_level": concerns[-1].get("level") if concerns else None,
                "incident_state": (inc or {}).get("state"),
                "spoken": s.lines()}
    finally:
        try:
            await asyncio.wait_for(s.close(), 10)
        except asyncio.TimeoutError:
            pass


async def main() -> None:
    out = [await one(p) for p in PHRASES]
    with open("/scratch/symptom_variants.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    for r in out:
        print(r["phrase"], "→ matcher:", r["matcher"], "| concern:", r["concern_level"],
              "| incident:", r["incident_state"])


asyncio.run(main())
