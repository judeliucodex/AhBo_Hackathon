"""Every model call goes through here (§20). Real or fake is chosen from AHBO_FAKE."""
from __future__ import annotations

from functools import lru_cache

from ahbo.core.adapters import FlashLLM
from ahbo.core.settings import get_settings


@lru_cache(maxsize=1)
def get_flash() -> FlashLLM:
    if get_settings().is_fake("models"):
        from .fakes import FakeFlash
        return FakeFlash()
    from .flash import RealFlash   # built in M4
    return RealFlash()


def reset_models_cache() -> None:
    get_flash.cache_clear()
