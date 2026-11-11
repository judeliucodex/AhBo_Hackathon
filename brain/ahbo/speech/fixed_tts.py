"""Fixed-clip TTS adapter choice (§10.3, §26.3): fake when `speech` is faked, else the provider configured in
`models.yaml → speech.tts.provider` (elevenlabs | azure). Fake mode is unchanged (silent clips, Azure voice name in
the manifest version), and a missing ElevenLabs key/voice falls back to the fake renderer with a warning."""
from __future__ import annotations

import logging
from typing import Any

from .config import speech_cfg

log = logging.getLogger("ahbo.speech")


def get_fixed_tts(settings: Any, config: Any = None):
    from .azure_tts import get_fixed_tts as azure_or_fake

    cfg = speech_cfg(config).tts
    if settings.is_fake("speech") or cfg.provider != "elevenlabs":
        return azure_or_fake(settings)
    voice_id = cfg.voice_id(settings)
    if not settings.elevenlabs_api_key or not voice_id:
        from .fakes import FakeTTS

        log.warning("speech.tts.provider=elevenlabs but ELEVENLABS_API_KEY/voice id is unset: silent fake clips")
        return FakeTTS(voice=settings.azure_tts_voice)
    from .elevenlabs_tts import ElevenLabsTTS

    return ElevenLabsTTS(settings.elevenlabs_api_key, voice_id, cfg.fixed_model, cfg.language or None)
