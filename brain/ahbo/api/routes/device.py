"""`WS /ws/device` (CONTRACTS §2) and `POST /session` (§10.2)."""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, WebSocket

from ahbo.core.events import Event

from ..auth import require_device_or_operator

log = logging.getLogger("ahbo.api.session")
router = APIRouter()

AGENT_NAME = "ahbo-agent"          # the LiveKit worker registers with this agent_name (explicit dispatch)
TOKEN_TTL_S = 600                  # 10 min (§10.2)


@router.websocket("/ws/device")
async def ws_device(ws: WebSocket) -> None:
    await ws.app.state.svc.hub.handle(ws)


def livekit_token(settings: Any, *, session_id: str, identity: str, reason: str, mode: str) -> dict[str, Any]:
    """Mint a 10-minute room token (livekit-api 1.2.2): join one room, publish the mic only, subscribe; the room
    config carries the metadata and dispatches the agent worker."""
    from livekit import api as lk

    room = f"ahbo-{session_id}"
    meta = json.dumps({"reason": reason, "mode": mode, "session_id": session_id}, ensure_ascii=False)
    grants = lk.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True, can_publish_data=True,
                            can_publish_sources=["microphone"])
    cfg = lk.RoomConfiguration(name=room, metadata=meta, empty_timeout=60, max_participants=3,
                               agents=[lk.RoomAgentDispatch(agent_name=AGENT_NAME, metadata=meta)])
    token = (lk.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
             .with_identity(identity).with_name("iPad").with_grants(grants).with_room_config(cfg)
             .with_metadata(meta).with_ttl(timedelta(seconds=TOKEN_TTL_S)).to_jwt())
    return {"livekit_url": settings.livekit_url, "token": token, "room": room, "expires_in": TOKEN_TTL_S}


@router.post("/session")
async def post_session(request: Request, body: dict[str, Any] = Body(default_factory=dict),
                       who: str = Depends(require_device_or_operator)) -> dict[str, Any]:
    svc = request.app.state.svc
    k = svc.kernel
    if k.state.muted:
        raise HTTPException(status_code=403, detail="muted")
    reason = str(body.get("reason") or "touch")[:32]
    mode = str(body.get("mode") or "companion")[:32]
    if k.state.session is None:
        await k.process(Event(type="session.open_request", source="device",
                              payload={"reason": reason, "mode": mode, "device_id": who}))
    s = k.state.session
    if s is None:
        raise HTTPException(status_code=409, detail="session_refused")
    if svc.settings.is_fake("livekit"):
        return {"text_only": True, "session_id": s.id}
    st = svc.settings
    if not (st.livekit_url and st.livekit_api_key and st.livekit_api_secret):
        raise HTTPException(status_code=503, detail="livekit_not_configured")
    out = livekit_token(st, session_id=s.id, identity=f"device-{who}", reason=s.reason, mode=s.mode)
    return {**out, "session_id": s.id}
