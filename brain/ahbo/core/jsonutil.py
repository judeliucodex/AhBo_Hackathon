from __future__ import annotations

import json
from typing import Any


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def loads(s: str | bytes | None, default: Any = None) -> Any:
    if s is None or s == "":
        return default
    return json.loads(s)
