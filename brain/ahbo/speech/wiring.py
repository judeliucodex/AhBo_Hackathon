"""speech wiring: put the fixed-clip TTS adapter on the services container (`svc.extra["fixed_tts"]`).

Nothing registers on the Kernel — TTS is used by `ops.render_fixed` at startup and by the voice worker while
speaking, never by a reducer (INV-3: safety audio never depends on the TTS adapter being up).
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("ahbo.speech")


def install(svc: Any) -> None:
    try:
        from .fixed_tts import get_fixed_tts

        svc.extra["fixed_tts"] = get_fixed_tts(svc.settings, getattr(svc, "config", None))
        log.info("speech: fixed-clip TTS adapter: %s", type(svc.extra["fixed_tts"]).__name__)
    except Exception:
        # no adapter is not fatal: render_fixed falls back to FakeTTS, safety audio never needs TTS (INV-3)
        log.exception("speech wiring could not build the TTS adapter; render_fixed will use FakeTTS")
