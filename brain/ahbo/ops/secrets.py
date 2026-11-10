"""`.env` management (§26.2, BUILD_LOG D-4). `.env` is mode 600; values are never printed or logged.

- `init_secrets`: creates `.env` from `.env.example` if missing, fills defaults, generates APP_SECRET,
  INTERNAL_TOKEN, SESSION_SECRET, TELEGRAM_WEBHOOK_SECRET, and — if no hash exists — a random operator password
  whose bcrypt hash goes into `.env` and whose plaintext is written once to
  `data/secrets/operator_password.initial` (mode 600). `set_password` replaces it and deletes that file.
- Values containing `$` (bcrypt hashes) are written single-quoted so Docker Compose takes them literally.
"""
from __future__ import annotations

import os
import re
import secrets as _secrets
from pathlib import Path

from ahbo.core.settings import REPO_ROOT

ENV_PATH = REPO_ROOT / ".env"
EXAMPLE_PATH = REPO_ROOT / ".env.example"
INITIAL_PW_PATH = REPO_ROOT / "data" / "secrets" / "operator_password.initial"
GENERATED = ("APP_SECRET", "INTERNAL_TOKEN", "SESSION_SECRET", "TELEGRAM_WEBHOOK_SECRET")
DEFAULTS = {
    "PUBLIC_BASE_URL": "https://srv2135273.hstgr.cloud",
    "AHBO_PROFILE": "demo",
    "AHBO_FAKE": "all",
    "TZ": "Asia/Hong_Kong",
    "AZURE_TTS_VOICE": "zh-HK-HiuMaanNeural",
    "AZURE_SPEECH_REGION": "eastasia",
    "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
}
LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    if " #" in v:
        v = v.split(" #", 1)[0].rstrip()
    return v


def _quote(v: str) -> str:
    if v == "" or re.fullmatch(r"[A-Za-z0-9_@%+=:,./~-]*", v):
        return v
    if "'" in v:
        raise ValueError("value contains a single quote")
    return f"'{v}'"


def read_env(path: Path = ENV_PATH) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        m = LINE.match(line)
        if m and not line.lstrip().startswith("#"):
            out[m.group(1)] = _unquote(m.group(2))
    return out


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def set_env_values(updates: dict[str, str], path: Path = ENV_PATH) -> None:
    """Rewrite `.env` in place: replace existing keys, append new ones; keep comments and order; mode 600."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    done: set[str] = set()
    for i, line in enumerate(lines):
        m = LINE.match(line)
        if m and not line.lstrip().startswith("#") and m.group(1) in updates:
            lines[i] = f"{m.group(1)}={_quote(updates[m.group(1)])}"
            done.add(m.group(1))
    for k, v in updates.items():
        if k not in done:
            lines.append(f"{k}={_quote(v)}")
    _write_private(path, "\n".join(lines) + "\n")


def gen_secret(nbytes: int = 32) -> str:
    return _secrets.token_urlsafe(nbytes)


def gen_password() -> str:
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "-".join("".join(_secrets.choice(alphabet) for _ in range(5)) for _ in range(4))


def init_secrets(path: Path = ENV_PATH, example: Path = EXAMPLE_PATH,
                 initial_pw_path: Path = INITIAL_PW_PATH) -> dict[str, list[str]]:
    """Idempotent. Returns the names (never values) of what was set."""
    if not path.exists():
        _write_private(path, example.read_text(encoding="utf-8") if example.exists() else "")
    env = read_env(path)
    updates: dict[str, str] = {}
    for k, v in DEFAULTS.items():
        if not env.get(k):
            updates[k] = v
    for k in GENERATED:
        if not env.get(k):
            updates[k] = gen_secret()
    created_pw = False
    if not env.get("OPERATOR_PASSWORD_HASH"):
        from ahbo.api.auth import hash_password
        pw = gen_password()
        updates["OPERATOR_PASSWORD_HASH"] = hash_password(pw)
        initial_pw_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(initial_pw_path.parent, 0o700)
        _write_private(initial_pw_path, pw + "\n")
        created_pw = True
    if updates:
        set_env_values(updates, path)
    else:
        os.chmod(path, 0o600)
    return {"set": sorted(updates), "initial_password_file": [str(initial_pw_path)] if created_pw else []}


def set_password(password: str, path: Path = ENV_PATH, initial_pw_path: Path = INITIAL_PW_PATH) -> None:
    from ahbo.api.auth import hash_password
    if len(password) < 10:
        raise ValueError("password must be at least 10 characters")
    set_env_values({"OPERATOR_PASSWORD_HASH": hash_password(password)}, path)
    initial_pw_path.unlink(missing_ok=True)
