"""Auth (§26.4, CONTRACTS §8): operator password login + signed session cookie, device tokens, internal token.

- Password: bcrypt hash in `OPERATOR_PASSWORD_HASH`; 5 login attempts per minute per client IP.
- Cookie `ahbo_session`: itsdangerous-signed with `SESSION_SECRET`, HttpOnly, Secure (https), SameSite=Strict, 12 h.
- Device token: `dt.<device_id>.<expiry>.<sig>` (HMAC-SHA256 keyed from `APP_SECRET`), 24 h.
- Internal token: `Authorization: Bearer <INTERNAL_TOKEN>`.
All secret comparisons are constant-time.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from typing import Any
from urllib.parse import quote

import bcrypt
from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ahbo.core.settings import Settings

COOKIE = "ahbo_session"
SESSION_TTL_S = 12 * 3600
DEVICE_TOKEN_TTL_S = 24 * 3600
LOGIN_LIMIT = 5
LOGIN_WINDOW_S = 60.0


# ---------- passwords ----------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    if not password or not hashed:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("ascii"))
    except ValueError:
        return False


# ---------- rate limit ----------
class RateLimiter:
    def __init__(self, limit: int = LOGIN_LIMIT, window_s: float = LOGIN_WINDOW_S):
        self.limit, self.window = limit, window_s
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


def client_ip(request: Any) -> str:
    """Caddy overwrites X-Forwarded-For with the real client (it trusts no incoming value), so the last entry is
    the client. Direct connections (tests, the Docker network) use the socket peer."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "?"


# ---------- operator session cookie ----------
def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    if not settings.session_secret:
        raise RuntimeError("SESSION_SECRET is not set")
    return URLSafeTimedSerializer(settings.session_secret, salt="ahbo-operator-session")


def make_session(settings: Settings) -> str:
    return _serializer(settings).dumps({"u": "operator", "n": secrets.token_hex(8)})


def check_session(settings: Settings, token: str | None) -> bool:
    if not token or not settings.session_secret:
        return False
    try:
        data = _serializer(settings).loads(token, max_age=SESSION_TTL_S)
    except (BadSignature, SignatureExpired):
        return False
    return isinstance(data, dict) and data.get("u") == "operator"


def set_session_cookie(response: Any, settings: Settings) -> None:
    response.set_cookie(COOKIE, make_session(settings), max_age=SESSION_TTL_S, path="/", httponly=True,
                        secure=settings.secure_cookies, samesite="strict")


def has_operator_cookie(settings: Settings, request: Any) -> bool:
    return check_session(settings, request.cookies.get(COOKIE))


# ---------- device tokens ----------
def _device_key(settings: Settings) -> bytes:
    if not settings.app_secret:
        raise RuntimeError("APP_SECRET is not set")
    return hashlib.sha256(b"ahbo-device-token:" + settings.app_secret.encode()).digest()


def _sig(settings: Settings, body: str) -> str:
    mac = hmac.new(_device_key(settings), body.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def make_device_token(settings: Settings, device_id: str, ttl_s: int = DEVICE_TOKEN_TTL_S,
                      now: float | None = None) -> str:
    exp = int((time.time() if now is None else now) + ttl_s)
    body = f"{device_id}.{exp}"
    return f"dt.{body}.{_sig(settings, body)}"


def check_device_token(settings: Settings, token: str | None, now: float | None = None) -> str | None:
    """Return the device id of a valid, unexpired token, else None."""
    if not token or not token.startswith("dt.") or not settings.app_secret:
        return None
    try:
        body, sig = token[3:].rsplit(".", 1)
        device_id, exp_s = body.rsplit(".", 1)
        exp = int(exp_s)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sig(settings, body)):
        return None
    if exp < (time.time() if now is None else now):
        return None
    return device_id


def bearer(request: Any) -> str | None:
    h = request.headers.get("authorization", "")
    if h[:7].lower() == "bearer ":
        return h[7:].strip()
    return None


def check_app_secret(settings: Settings, token: str | None) -> bool:
    return bool(token and settings.app_secret and hmac.compare_digest(token.encode(), settings.app_secret.encode()))


def check_internal(settings: Settings, token: str | None) -> bool:
    return bool(token and settings.internal_token
                and hmac.compare_digest(token.encode(), settings.internal_token.encode()))


# ---------- FastAPI dependencies ----------
def _settings(request: Request) -> Settings:
    return request.app.state.svc.settings


class LoginRequired(Exception):
    """Raised by page dependencies; turned into a redirect to /login by the app."""

    def __init__(self, next_path: str):
        self.next_path = next_path


def require_operator_page(request: Request) -> None:
    if not has_operator_cookie(_settings(request), request):
        raise LoginRequired(request.url.path)


def require_operator(request: Request) -> None:
    if not has_operator_cookie(_settings(request), request):
        raise HTTPException(status_code=401, detail="login required")


def require_device_or_operator(request: Request) -> str:
    """Device token or operator cookie. Returns the device id (or 'operator')."""
    s = _settings(request)
    dev = check_device_token(s, bearer(request))
    if dev:
        return dev
    if has_operator_cookie(s, request):
        return "operator"
    raise HTTPException(status_code=401, detail="device token or login required")


def require_internal(request: Request) -> None:
    if not check_internal(_settings(request), bearer(request)):
        raise HTTPException(status_code=401, detail="internal token required")


def login_redirect_url(next_path: str) -> str:
    return "/login?next=" + quote(next_path, safe="/")


def safe_next(next_path: str | None) -> str:
    if not next_path or not next_path.startswith("/") or next_path.startswith("//") or "\\" in next_path:
        return "/operator/"
    return next_path
