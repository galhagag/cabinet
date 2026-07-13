"""Live room stream: /ws/rooms/{room_id} over the in-process WS hub."""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import get_settings
from ..db.base import get_sessionmaker
from ..db.models import Room
from ..services.entra_auth import EntraTokenError, EntraTokenValidator
from ..services.realtime import ConnectionManager
from .deps import DEFAULT_DEV_EMAIL, is_room_member

router = APIRouter()

WS_FORBIDDEN = 4403  # application close code mirroring HTTP 403


@router.websocket("/ws/rooms/{room_id}")
async def room_stream(websocket: WebSocket, room_id: str) -> None:
    # Dev: same identity scheme as HTTP (X-User-Email header). Production
    # (CABINET_AUTH_MODE=entra): browsers cannot set custom headers on the
    # WebSocket handshake, so the client passes the Entra ID access token as
    # a query parameter instead; it is verified the same way as the HTTP
    # bearer token before the connection is accepted.
    settings = get_settings()
    if settings.auth_mode == "entra":
        token = websocket.query_params.get("access_token")
        validator: EntraTokenValidator | None = websocket.app.state.entra_validator
        if not token or validator is None:
            await websocket.close(code=WS_FORBIDDEN)
            return
        try:
            user_email = await validator.validate(token)
        except EntraTokenError:
            await websocket.close(code=WS_FORBIDDEN)
            return
    else:
        user_email = websocket.headers.get("x-user-email", DEFAULT_DEV_EMAIL)

    async with get_sessionmaker()() as session:
        room = await session.get(Room, room_id)
        allowed = room is not None and await is_room_member(
            session, room_id, user_email
        )
    if not allowed:
        await websocket.close(code=WS_FORBIDDEN)
        return

    manager: ConnectionManager = websocket.app.state.manager
    await manager.connect(room_id, websocket)
    try:
        while True:
            text = await websocket.receive_text()
            if text == "ping":  # lightweight client keepalive
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(room_id, websocket)
