"""Room logo API: serves the stored image and accepts a manual override upload."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.orchestrator import RealtimeBroker
from ..db.base import get_session
from ..db.models import Room
from ..schemas import RoomLogoOut
from ..services.blob_storage import BlobStorageProvider
from ..services.logo import LogoService
from .deps import (
    get_blob_provider,
    get_broker,
    get_logo_service,
    require_room_member,
    require_room_member_allow_query_token,
)

router = APIRouter(prefix="/api/rooms/{room_id}/logo", tags=["room-logo"])

_EXT_CONTENT_TYPE = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}


async def _get_room(session: AsyncSession, room_id: str) -> Room:
    room = await session.get(Room, room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="room not found")
    return room


@router.get("")
async def get_room_logo(
    room_id: str,
    session: AsyncSession = Depends(get_session),
    blob: BlobStorageProvider = Depends(get_blob_provider),
    _member: str = Depends(require_room_member_allow_query_token),
) -> Response:
    room = await _get_room(session, room_id)
    if room.logo_blob_path is None:
        raise HTTPException(status_code=404, detail="room has no logo")
    ext = room.logo_blob_path.rsplit(".", 1)[-1]
    data = await blob.download(room.logo_blob_path)
    return Response(
        content=data, media_type=_EXT_CONTENT_TYPE.get(ext, "application/octet-stream")
    )


@router.post("", response_model=RoomLogoOut)
async def upload_room_logo(
    room_id: str,
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
    logo_service: LogoService = Depends(get_logo_service),
    broker: RealtimeBroker = Depends(get_broker),
    user_email: str = Depends(require_room_member),
) -> RoomLogoOut:
    room = await _get_room(session, room_id)
    data = await file.read()
    try:
        await logo_service.save_upload(
            session, room, content_type=file.content_type, data=data, actor=user_email
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = RoomLogoOut(logo_url=f"/api/rooms/{room_id}/logo", logo_source=room.logo_source)
    await broker.publish(
        room_id,
        {
            "type": "room_logo_updated",
            "room_id": room_id,
            "logo_url": result.logo_url,
            "logo_source": result.logo_source,
        },
    )
    return result
