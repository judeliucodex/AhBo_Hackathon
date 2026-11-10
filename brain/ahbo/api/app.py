"""FastAPI app factory: `uvicorn ahbo.api.app:create_app --factory`.

The services container is built inside the lifespan (on the server's event loop). Routes are registered in this
order: component routers (webhooks, mock-centre API, …), then the platform's API routes, then the static
folders last, so a component route under a shared prefix always wins over a static catch-all.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ahbo.core.clock import Clock
from ahbo.core.settings import Settings, get_settings

from .auth import LoginRequired, RateLimiter, login_redirect_url
from .services import Services, build_services

log = logging.getLogger("ahbo.api")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}


class SecurityHeaders:
    """Pure ASGI middleware (does not buffer SSE streams; websockets pass through)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapped(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                have = {k.lower() for k, _ in headers}
                for k, v in (*SECURITY_HEADERS.items(), ("X-Frame-Options", "SAMEORIGIN")):
                    if k.lower().encode() not in have:
                        headers.append((k.lower().encode(), v.encode()))
                message = {**message, "headers": headers}
            await send(message)

        return await self.app(scope, receive, send_wrapped)


def _configure_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=os.environ.get("AHBO_LOG_LEVEL", "INFO"),
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(settings: Settings | None = None, *, db_path: str | Path | None = None, clock: Clock | None = None,
               time_scale: float = 1.0, backups: bool | None = None, services: Services | None = None,
               **build_kw: Any) -> FastAPI:
    _configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = services or build_services(settings or get_settings(), db_path=db_path, clock=clock,
                                         time_scale=time_scale, **build_kw)
        app.state.svc = svc
        _register_routes(app, svc)
        await svc.start()
        svc.extra["started_at"] = svc.clock.real_now().isoformat(timespec="seconds")
        do_backup = backups if backups is not None else (db_path is None and str(svc.db.path) != ":memory:")
        if do_backup:
            from .backup import nightly_loop
            svc.extra["backup_task"] = asyncio.get_running_loop().create_task(
                nightly_loop(Path(svc.db.path), svc.settings.data_dir / "backups"))
        log.info("api up: fake=%s profile=%s components=%s reducers=%s", sorted(svc.settings.fake),
                 svc.settings.profile, svc.installed, [n for n, _ in svc.kernel.reducers])
        try:
            yield
        finally:
            t = svc.extra.get("backup_task")
            if t:
                t.cancel()
            await svc.stop()

    app = FastAPI(title="Ah Bo", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.add_middleware(SecurityHeaders)
    app.state.login_limiter = RateLimiter()      # 5 attempts / min / IP (§26.4)

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired):
        return RedirectResponse(login_redirect_url(exc.next_path), status_code=303)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse({"ok": False, "reason": "internal_error"}, status_code=500)

    return app


def _register_routes(app: FastAPI, svc: Services) -> None:
    if getattr(app.state, "routes_registered", False):
        return
    from .routes import device, internal, media, operator, public
    for r in svc.routers:
        app.include_router(r)
    app.include_router(public.router)
    app.include_router(device.router)
    app.include_router(media.router)
    app.include_router(operator.router)
    app.include_router(internal.router)
    app.include_router(public.static_router())
    app.state.routes_registered = True
