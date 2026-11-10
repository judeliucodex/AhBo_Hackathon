"""Ids: `<prefix>_<ULID>` (CONTRACTS §1)."""
from __future__ import annotations

from ulid import ULID


def ulid() -> str:
    return str(ULID())


def new_id(prefix: str) -> str:
    return f"{prefix}_{ULID()}"
