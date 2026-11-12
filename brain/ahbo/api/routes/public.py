"""Health, login/logout, `/` and the static folders (/face/, /sim/, /operator/, /mock-centre/). BUILD_LOG D-3."""
from __future__ import annotations

import html
import mimetypes
import tomllib
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from ahbo.core.settings import BRAIN_DIR, REPO_ROOT, SEED_DIR

from ..auth import (
    COOKIE,
    check_session,
    client_ip,
    require_operator_page,
    safe_next,
    set_session_cookie,
    verify_password,
)

router = APIRouter()
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("audio/mp4", ".m4a")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/json", ".json")


@lru_cache(maxsize=1)
def version() -> str:
    try:
        return tomllib.loads((BRAIN_DIR / "pyproject.toml").read_text())["project"]["version"]
    except Exception:
        return "0.1.0"


@router.get("/healthz")
async def healthz(request: Request) -> dict:
    s = request.app.state.svc.settings
    return {"ok": True, "version": version(), "fake": sorted(s.fake), "profile": s.profile}


# ---------------------------------------------------------------- login
LOGIN_HTML = """<!doctype html>
<html lang="zh-HK"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>阿寶 · 操作台登入 Operator login</title>
<style>
:root{{--bg:#0f1220;--card:#181c2e;--fg:#f2efe6;--mut:#9aa0b8;--acc:#ffb35c;--err:#ff7a7a}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);
color:var(--fg);font-family:"M PLUS Rounded 1c","PingFang HK","Noto Sans HK",system-ui,sans-serif}}
form{{background:var(--card);padding:32px 28px;border-radius:22px;width:min(360px,92vw);
box-shadow:0 20px 60px rgba(0,0,0,.35)}}h1{{margin:0 0 4px;font-size:22px}}p{{margin:0 0 20px;color:var(--mut);font-size:14px}}
label{{display:block;font-size:14px;color:var(--mut);margin-bottom:6px}}
input{{width:100%;padding:12px 14px;border-radius:12px;border:1px solid #2c3250;background:#0f1220;color:var(--fg);font-size:16px}}
button{{margin-top:18px;width:100%;padding:12px;border:0;border-radius:12px;background:var(--acc);color:#1a1a1a;
font-size:16px;font-weight:700;cursor:pointer}}.err{{color:var(--err);font-size:14px;margin-top:12px}}
</style></head><body>
<form method="post" action="/login">
<h1>阿寶 操作台</h1><p>Ah Bo operator console</p>
<label for="pw">密碼 Password</label>
<input id="pw" name="password" type="password" autocomplete="current-password" autofocus required>
<input type="hidden" name="next" value="{next}">
<button type="submit">登入 Log in</button>{error}
</form></body></html>"""


def login_page(next_path: str, error: str = "", status: int = 200) -> HTMLResponse:
    err = f'<div class="err">{html.escape(error)}</div>' if error else ""
    return HTMLResponse(LOGIN_HTML.format(next=html.escape(safe_next(next_path)), error=err), status_code=status,
                        headers={"Cache-Control": "no-store"})


@router.get("/login")
async def login_get(request: Request, next: str = "/sim/") -> Response:
    if check_session(request.app.state.svc.settings, request.cookies.get(COOKIE)):
        return RedirectResponse(safe_next(next), status_code=303)
    return login_page(next)


@router.post("/login")
async def login_post(request: Request, password: str = Form(""), next: str = Form("/sim/")) -> Response:
    settings = request.app.state.svc.settings
    if not request.app.state.login_limiter.allow(client_ip(request)):
        return login_page(next, "嘗試太多次，請一分鐘後再試。Too many attempts — wait a minute.", 429)
    if not verify_password(password, settings.operator_password_hash):
        return login_page(next, "密碼唔啱。Wrong password.", 401)
    resp = RedirectResponse(safe_next(next), status_code=303)
    set_session_cookie(resp, settings)
    return resp


@router.post("/logout")
async def logout() -> Response:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE, path="/")
    return resp


@router.get("/", dependencies=[Depends(require_operator_page)])
async def root() -> Response:
    return RedirectResponse("/sim/", status_code=302)   # the web console is dropped (owner, 11 Nov): Telegram is it


# ---------------------------------------------------------------- static folders
def _serve(base: Path, path: str, index: tuple[str, ...] = ("index.html",)) -> Response:
    base = base.resolve()
    target = (base / path).resolve() if path else base
    if target != base and base not in target.parents:
        return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)
    if target.is_dir():
        for name in index:
            if (target / name).is_file():
                target = target / name
                break
        else:
            return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)
    if not target.is_file():
        return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)
    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if ctype.startswith("text/") or ctype in ("application/json", "image/svg+xml"):
        ctype += "; charset=utf-8"
    headers = {"Cache-Control": "no-cache"}
    return FileResponse(target, media_type=ctype, headers=headers)


def static_router() -> APIRouter:
    """Added last, so component routes under the same prefixes win."""
    r = APIRouter()
    face, sim, op, mock = (REPO_ROOT / "face", REPO_ROOT / "sim", REPO_ROOT / "operator-ui", REPO_ROOT / "mock-centre")

    for prefix in ("face", "sim", "operator", "mock-centre"):
        def _redir(prefix: str = prefix) -> Response:
            return RedirectResponse(f"/{prefix}/", status_code=308)
        r.add_api_route(f"/{prefix}", _redir, methods=["GET"], include_in_schema=False)

    @r.get("/face/{path:path}", include_in_schema=False)
    async def face_files(path: str = "") -> Response:
        return _serve(face, path)

    @r.get("/mock-centre/{path:path}", include_in_schema=False)
    async def mock_files(path: str = "") -> Response:
        return _serve(mock, path)

    @r.get("/sim/{path:path}", include_in_schema=False, dependencies=[Depends(require_operator_page)])
    async def sim_files(path: str = "") -> Response:
        if path == "fall_placeholder.jpg" and not (sim / path).is_file():
            return _serve(SEED_DIR, path)        # PRD §26.3: the sim uploads seed/fall_placeholder.jpg (QA 25 D10)
        return _serve(sim, path, ("index.html", "ipad.html"))

    @r.get("/operator/{path:path}", include_in_schema=False, dependencies=[Depends(require_operator_page)])
    async def operator_files(path: str = "") -> Response:
        return _serve(op, path)

    return r
