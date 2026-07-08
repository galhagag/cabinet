"""Live room stream: /ws/rooms/{room_id} over the in-process WS hub."""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..db.base import get_sessionmaker
from ..db.models import Room
from ..services.realtime import ConnectionManager
from .deps import DEFAULT_DEV_EMAIL, is_room_member

router = APIRouter()

WS_FORBIDDEN = 4403  # application close code mirroring HTTP 403


@router.websocket("/ws/rooms/{room_id}")
async def room_stream(websocket: WebSocket, room_id: str) -> None:
    # Same dev identity scheme as HTTP (X-User-Email header); production
    # replaces this with an Entra ID token on the connect handshake.
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
        manager.disconnect(room_id, websocket)
