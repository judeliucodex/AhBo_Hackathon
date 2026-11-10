"""Config loader (§26.5). Files live in brain/config/; `{PUBLIC_BASE_URL}` is substituted at load time."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .settings import CONFIG_DIR, SEED_DIR, get_settings


def _subst(obj: Any, base: str) -> Any:
    if isinstance(obj, str):
        return obj.replace("{PUBLIC_BASE_URL}", base)
    if isinstance(obj, list):
        return [_subst(x, base) for x in obj]
    if isinstance(obj, dict):
        return {k: _subst(v, base) for k, v in obj.items()}
    return obj


def load_yaml(path: Path, base_url: str | None = None) -> Any:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _subst(data, base_url if base_url is not None else get_settings().public_base_url)


@dataclass
class Config:
    fixed_lines: dict[str, str]
    ok_phrases: dict[str, Any]
    redflags: dict[str, list[str]]
    caps: dict[str, Any]
    checkins: dict[str, Any]
    action_domains: dict[str, Any]
    wake_match: dict[str, Any]
    wake_cases: dict[str, list[str]]
    phrases: list[str]
    models: dict[str, Any]
    ui_steps: dict[str, Any]

    def fixed_text(self, clip_id: str) -> str:
        return self.fixed_lines[clip_id]


_cache: Config | None = None


def load_config(config_dir: Path | None = None, base_url: str | None = None, *, fresh: bool = False) -> Config:
    global _cache
    if _cache is not None and not fresh and config_dir is None:
        return _cache
    d = config_dir or CONFIG_DIR
    y = lambda name: load_yaml(d / name, base_url)  # noqa: E731
    cfg = Config(
        fixed_lines=y("fixed_lines.yaml"),
        ok_phrases=y("ok_phrases.yaml"),
        redflags=y("redflags.yaml"),
        caps=y("caps.yaml"),
        checkins=y("checkins.yaml"),
        action_domains=y("action_domains.yaml"),
        wake_match=y("wake_match.yaml"),
        wake_cases=y("wake_cases.yaml"),
        phrases=y("phrases.yaml"),
        models=y("models.yaml"),
        ui_steps=y("ui_steps.yaml"),
    )
    if config_dir is None:
        _cache = cfg
    return cfg


def load_seed(name: str = "demo.yaml", base_url: str | None = None) -> dict[str, Any]:
    return load_yaml(SEED_DIR / name, base_url)
