"""Media (CONTRACTS §8): fall photo upload, fixed clips, voice notes, console screenshots, the public phone clip."""
from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from ahbo.core.ids import new_id

from ..auth import bearer, check_device_token, has_operator_cookie, require_device_or_operator, require_operator
from ..services import fixed_dir

router = APIRouter()
MAX_PHOTO_BYTES = 8 * 1024 * 1024
PHOTO_DAYS = 7
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
RANDOM_DIR = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


def _file_in(base: Path, name: str, media_type: str | None = None) -> FileResponse:
    if not SAFE_NAME.match(name) or name.startswith("."):
        raise HTTPException(status_code=404)
    base = base.resolve()
    p = (base / name).resolve()
    if base not in p.parents or not p.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(p, media_type=media_type, headers={"Cache-Control": "private, max-age=300"})


@router.post("/media/photo")
async def upload_photo(request: Request, file: UploadFile = File(...), incident_id: str = Form(...),
                       who: str = Depends(require_device_or_operator)) -> dict:
    svc = request.app.state.svc
    data = await file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail="too_large")
    if not (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(status_code=415, detail="jpeg_or_png_only")
    if not re.match(r"^[A-Za-z0-9_]{1,64}$", incident_id):
        raise HTTPException(status_code=422, detail="bad_incident_id")
    photo_id = new_id("ph")
    ext = ".jpg" if data[:3] == b"\xff\xd8\xff" else ".png"
    d = svc.settings.media_dir / "incidents"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{photo_id}{ext}"
    path.write_bytes(data)
    now = svc.clock.now()
    svc.db.execute("INSERT INTO media(id, kind, path, incident_id, created_at, expires_at) VALUES (?,?,?,?,?,?)",
                   (photo_id, "photo", str(path), incident_id, now.isoformat(),
                    (now + timedelta(days=PHOTO_DAYS)).isoformat()))
    return {"ok": True, "photo_id": photo_id}


def _photo_access(request: Request, svc, photo_id: str) -> None:
    """Operator cookie: any photo. Device token: only photos a life story shows on the iPad (`life_stories.photo_id`,
    the story card's `photo_url`); incident (fall) photos stay console-only (§3.3)."""
    s = svc.settings
    if has_operator_cookie(s, request):
        return
    if check_device_token(s, bearer(request)):
        if svc.db.one("SELECT 1 AS x FROM life_stories WHERE photo_id=? LIMIT 1", (photo_id,)):
            return
        raise HTTPException(status_code=403, detail="console_only")
    raise HTTPException(status_code=401, detail="login required")


@router.get("/media/photo/{photo_id}")
async def get_photo(request: Request, photo_id: str) -> FileResponse:
    svc = request.app.state.svc
    _photo_access(request, svc, photo_id)
    row = svc.db.one("SELECT path FROM media WHERE id=? AND kind='photo'", (photo_id,))
    if not row:
        raise HTTPException(status_code=404)
    p = Path(row["path"])
    if not p.is_absolute():
        p = svc.settings.data_dir / p
    return _file_in(p.parent, p.name)


@router.get("/media/fixed/manifest.json", dependencies=[Depends(require_device_or_operator)])
async def fixed_manifest(request: Request):
    p = fixed_dir(request.app.state.svc) / "manifest.json"
    if not p.is_file():
        return JSONResponse({"ok": False, "reason": "not_rendered"}, status_code=404)
    return FileResponse(p, media_type="application/json", headers={"Cache-Control": "no-cache"})


@router.get("/media/fixed/{name}", dependencies=[Depends(require_device_or_operator)])
async def fixed_clip(request: Request, name: str) -> FileResponse:
    return _file_in(fixed_dir(request.app.state.svc), name)


@router.get("/media/voice/{voice_id}", dependencies=[Depends(require_device_or_operator)])
async def voice_note(request: Request, voice_id: str) -> FileResponse:
    svc = request.app.state.svc
    row = svc.db.one("SELECT file_path, mime FROM voice_messages WHERE id=?", (voice_id,))
    if not row or not row["file_path"]:
        raise HTTPException(status_code=404)
    p = Path(row["file_path"])
    if not p.is_absolute():
        p = svc.settings.data_dir / p
    # Seed rows may have been written with another checkout's absolute path: fall back to the repo seed folder.
    if not p.is_file():
        from ahbo.core.settings import SEED_DIR
        alt = SEED_DIR / "voice" / p.name
        p = alt if alt.is_file() else p
    if not p.is_file():
        raise HTTPException(status_code=404)
    return _file_in(p.parent, p.name, row["mime"] or "audio/mp4")


@router.get("/media/screens/{name}", dependencies=[Depends(require_operator)])
async def screens(request: Request, name: str) -> FileResponse:
    return _file_in(request.app.state.svc.settings.media_dir / "screens", name)


@router.get("/media/public/{rand}/{name}")
async def public_clip(request: Request, rand: str, name: str) -> FileResponse:
    svc = request.app.state.svc
    # Only the current clip dir is served (QA 26 D1): an old or wiped dir name is a 404 even if files linger.
    if not RANDOM_DIR.match(rand) or rand != svc.db.kv_get("public_clip_rand"):
        raise HTTPException(status_code=404)
    return _file_in(svc.settings.media_dir / "public" / rand, name)
