"""USD per million tokens, used for the cost guard and the `costs` table. VERIFY against the providers' price pages
before relying on the numbers (the owner's accounts may differ); they only steer caps, never behaviour."""
from __future__ import annotations

from typing import Any

PER_MTOK: dict[str, dict[str, float]] = {
    # model: input, output, cache_read, cache_write
    "claude-sonnet-5-5": {"in": 3.0, "out": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-5-5": {"in": 1.0, "out": 5.0, "cache_read": 0.10, "cache_write": 1.25},
    "deepseek-flash": {"in": 0.27, "out": 1.10, "cache_read": 0.07, "cache_write": 0.27},
}
WEB_SEARCH_USD = 0.01        # per search (§20.2: VERIFY the current price)
DEFAULT = {"in": 3.0, "out": 15.0, "cache_read": 0.30, "cache_write": 3.75}


def usd(model: str, *, input_tokens: int = 0, output_tokens: int = 0, cache_read: int = 0, cache_write: int = 0,
        web_searches: int = 0) -> float:
    p: dict[str, Any] = next((v for k, v in PER_MTOK.items() if k in (model or "")), DEFAULT)
    return (input_tokens * p["in"] + output_tokens * p["out"] + cache_read * p["cache_read"]
            + cache_write * p["cache_write"]) / 1_000_000 + web_searches * WEB_SEARCH_USD
