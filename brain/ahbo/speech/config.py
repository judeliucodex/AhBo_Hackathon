"""Speech provider config: the `speech:` section of `config/models.yaml` (owner decision 11 Nov 2026: ElevenLabs).

One place for provider + model ids; the voice worker, the fixed-clip renderer, the Telegram voice-note transcriber
and the doctor all read it here (no model id is hard-coded anywhere else). Defaults below only fill a missing key.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

API_BASE = "https://api.elevenlabs.io/v1"
MAX_KEYTERMS_LIVE = 50          # realtime limit (plugin docstring, livekit-plugins-elevenlabs 1.8.11)
MAX_KEYTERM_CHARS = 50
MAX_KEYTERM_SPACES = 4


@dataclass(frozen=True)
class SttCfg:
    provider: str = "elevenlabs"
    live_model: str = "scribe_v3_live"
    file_model: str = "scribe_v2"
    language: str = "yue"
    use_keyterms: bool = True
    keyterms: tuple[str, ...] = ()

    def active_keyterms(self, live: bool = False) -> list[str]:
        """Keyterms to send (empty when switched off), trimmed to the API limits."""
        if not self.use_keyterms:
            return []
        out = [k for k in self.keyterms
               if k and len(k) <= MAX_KEYTERM_CHARS and k.count(" ") <= MAX_KEYTERM_SPACES]
        return out[:MAX_KEYTERMS_LIVE] if live else out


@dataclass(frozen=True)
class TtsCfg:
    provider: str = "elevenlabs"
    voice_id_env: str = "ELEVENLABS_VOICE_ID"
    language: str = "yue"
    live_model: str = "eleven_v4_turbo"
    fixed_model: str = "eleven_v4"
    azure_fallback: bool = True

    def voice_id(self, settings: Any = None) -> str:
        if self.voice_id_env == "ELEVENLABS_VOICE_ID" and settings is not None:
            return str(getattr(settings, "elevenlabs_voice_id", "") or "")
        return os.environ.get(self.voice_id_env, "").strip()


@dataclass(frozen=True)
class SpeechCfg:
    stt: SttCfg = field(default_factory=SttCfg)
    tts: TtsCfg = field(default_factory=TtsCfg)


def _pick(cls: type, raw: dict[str, Any]) -> Any:
    known = {k: v for k, v in (raw or {}).items() if k in cls.__dataclass_fields__ and v is not None}
    if "keyterms" in known:
        known["keyterms"] = tuple(str(x) for x in known["keyterms"] or [])
    return cls(**known)


def speech_cfg(config: Any = None) -> SpeechCfg:
    """The speech section of the loaded config (`load_config()` when None)."""
    if config is None:
        from ahbo.core.config import load_config
        config = load_config()
    models = config.models if hasattr(config, "models") else config
    sec = (models or {}).get("speech") or {}
    return SpeechCfg(stt=_pick(SttCfg, sec.get("stt") or {}), tts=_pick(TtsCfg, sec.get("tts") or {}))
