"""Environment settings (§26.2). Secrets are read from the environment (`.env` via compose) and never logged."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(os.environ.get("AHBO_REPO", Path(__file__).resolve().parents[3]))
BRAIN_DIR = REPO_ROOT / "brain"
CONFIG_DIR = BRAIN_DIR / "config"
SEED_DIR = BRAIN_DIR / "seed"

FAKE_ITEMS = ("models", "speech", "livekit", "telegram", "twilio", "vision")
EMERGENCY_NUMBERS = ("999", "112", "911")  # INV-9: rejected outright, in any formatting


def mask(secret: str | None) -> str:
    """Show at most the last 4 characters of a secret (§11, §15)."""
    if not secret:
        return "(unset)"
    return "…" + secret[-4:] if len(secret) > 8 else "…set"


def parse_fake(raw: str | None) -> frozenset[str]:
    raw = (raw if raw is not None else "all").strip().lower()
    if raw in ("", "all"):
        return frozenset(FAKE_ITEMS)
    if raw == "none":
        return frozenset()
    items = {x.strip() for x in raw.split(",") if x.strip()}
    unknown = items - set(FAKE_ITEMS)
    if unknown:
        raise ValueError(f"AHBO_FAKE has unknown items: {sorted(unknown)}")
    return frozenset(items)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    public_base_url: str
    profile: str
    fake: frozenset[str]
    tz: str
    data_dir: Path
    app_secret: str
    internal_token: str
    session_secret: str
    operator_password_hash: str
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    anthropic_api_key: str = ""
    opper_api_key: str = ""
    azure_speech_key: str = ""
    azure_speech_region: str = "eastasia"
    azure_tts_voice: str = "zh-HK-HiuMaanNeural"
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_model_id: str = ""
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    family_telegram_chat_ids: dict[str, str] = field(default_factory=dict)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from: str = ""
    emergency_sim_phone: str = ""
    family_phone: str = ""
    beacon_tokens: dict[str, str] = field(default_factory=dict)
    api_internal_url: str = "http://api:8000"
    browser_ws: str = "ws://browser:3000/"

    def is_fake(self, item: str) -> bool:
        assert item in FAKE_ITEMS, item
        return item in self.fake

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ahbo.sqlite"

    @property
    def secure_cookies(self) -> bool:
        return self.public_base_url.startswith("https://")


def _pairs(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in raw.split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def load_settings() -> Settings:
    return Settings(
        public_base_url=_env("PUBLIC_BASE_URL", "https://srv2135273.hstgr.cloud").rstrip("/"),
        profile=_env("AHBO_PROFILE", "demo") or "demo",
        fake=parse_fake(os.environ.get("AHBO_FAKE")),
        tz=_env("TZ", "Asia/Hong_Kong"),
        data_dir=Path(_env("AHBO_DATA_DIR", str(REPO_ROOT / "data"))),
        app_secret=_env("APP_SECRET"),
        internal_token=_env("INTERNAL_TOKEN"),
        session_secret=_env("SESSION_SECRET"),
        operator_password_hash=_env("OPERATOR_PASSWORD_HASH"),
        deepseek_api_key=_env("DEEPSEEK_API_KEY"),
        deepseek_base_url=_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com") or "https://api.deepseek.com",
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        opper_api_key=_env("OPPER_API_KEY"),
        azure_speech_key=_env("AZURE_SPEECH_KEY"),
        azure_speech_region=_env("AZURE_SPEECH_REGION", "eastasia") or "eastasia",
        azure_tts_voice=_env("AZURE_TTS_VOICE", "zh-HK-HiuMaanNeural") or "zh-HK-HiuMaanNeural",
        elevenlabs_api_key=_env("ELEVENLABS_API_KEY"),
        elevenlabs_voice_id=_env("ELEVENLABS_VOICE_ID"),
        elevenlabs_model_id=_env("ELEVENLABS_MODEL_ID"),
        livekit_url=_env("LIVEKIT_URL"),
        livekit_api_key=_env("LIVEKIT_API_KEY"),
        livekit_api_secret=_env("LIVEKIT_API_SECRET"),
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
        telegram_webhook_secret=_env("TELEGRAM_WEBHOOK_SECRET"),
        family_telegram_chat_ids=_pairs(_env("FAMILY_TELEGRAM_CHAT_IDS")),
        twilio_account_sid=_env("TWILIO_ACCOUNT_SID"),
        twilio_auth_token=_env("TWILIO_AUTH_TOKEN"),
        twilio_from=_env("TWILIO_FROM"),
        emergency_sim_phone=_env("EMERGENCY_SIM_PHONE"),
        family_phone=_env("FAMILY_PHONE"),
        beacon_tokens=_pairs(_env("BEACON_TOKENS")),
        api_internal_url=_env("AHBO_API_INTERNAL_URL", "http://api:8000") or "http://api:8000",
        browser_ws=_env("AHBO_BROWSER_WS", "ws://browser:3000/") or "ws://browser:3000/",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
