"""Live room stream: /ws/rooms/{room_id} over the in-process WS hub."""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..services.realtime import ConnectionManager

router = APIRouter()


@router.websocket("/ws/rooms/{room_id}")
async def room_stream(websocket: WebSocket, room_id: str) -> None:
    manager: ConnectionManager = websocket.app.state.manager
    await manager.connect(room_id, websocket)
    try:
        while True:
            text = await websocket.receive_text()
            if text == "ping":  # lightweight client keepalive
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        manager.disconnect(room_id, websocket)
