"""Every model call goes through here (§20). Real or fake is chosen from AHBO_FAKE (`models`, `vision`)."""
from __future__ import annotations

from functools import lru_cache

from ahbo.core.adapters import DeepRunner, FlashLLM, Vision
from ahbo.core.settings import get_settings


@lru_cache(maxsize=1)
def get_flash() -> FlashLLM:
    if get_settings().is_fake("models"):
        from .fakes import FakeFlash
        return FakeFlash()
    from .flash import RealFlash
    return RealFlash(get_settings())


def get_deep(*, ctx=None, time_scale: float = 1.0) -> DeepRunner:
    """FakeDeep (2.5 s × time_scale) when models are faked, else the Sonnet loop with read-only tools."""
    s = get_settings()
    if s.is_fake("models"):
        from .fakes import FakeDeep
        return FakeDeep(delay_s=2.5 * time_scale)
    from ahbo.deep.loop import DeepLoop
    return DeepLoop(s, ctx=ctx)


def get_vision(*, time_scale: float = 1.0) -> Vision:
    s = get_settings()
    if s.is_fake("vision"):
        from .fakes import FakeVision
        return FakeVision(delay_s=1.5 * time_scale)
    from .vision import RealVision
    return RealVision(s)


def reset_models_cache() -> None:
    get_flash.cache_clear()
