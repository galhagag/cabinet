"""Google Drive API: OAuth2 authorize/callback, folder linking, status."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.orchestrator import RealtimeBroker
from ..db.base import get_session
from ..db.models import AuditLog, GDriveConnection, Room
from ..schemas import GDriveAuthorizeOut, GDriveFolderLink, GDriveStatusOut
from ..services.google_oauth import GoogleOAuthService
from .deps import get_broker, get_current_user_email, get_google_oauth

router = APIRouter(tags=["gdrive"])


async def _get_connection(
    session: AsyncSession, room_id: str
) -> GDriveConnection | None:
    result = await session.execute(
        select(GDriveConnection).where(GDriveConnection.room_id == room_id)
    )
    return result.scalar_one_or_none()


def _status_out(conn: GDriveConnection) -> GDriveStatusOut:
    return GDriveStatusOut(
        status=conn.status,
        google_folder_id=conn.google_folder_id,
        google_folder_name=conn.google_folder_name,
        token_expiry=conn.token_expiry,
        scopes=conn.scopes,
    )


@router.get(
    "/api/rooms/{room_id}/gdrive/authorize", response_model=GDriveAuthorizeOut
)
async def gdrive_authorize(
    room_id: str,
    session: AsyncSession = Depends(get_session),
    google_oauth: GoogleOAuthService = Depends(get_google_oauth),
    user_email: str = Depends(get_current_user_email),
) -> GDriveAuthorizeOut:
    room = await session.get(Room, room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="room not found")

    conn = await _get_connection(session, room_id)
    if conn is None:
        conn = GDriveConnection(room_id=room_id, status="pending")
        session.add(conn)
    else:
        conn.status = "pending"
    await session.commit()

    url, state = await google_oauth.authorize_url(room_id, user_email)
    return GDriveAuthorizeOut(authorize_url=url, state=state)


@router.get("/api/gdrive/callback")
async def gdrive_callback(
    code: str,
    state: str,
    session: AsyncSession = Depends(get_session),
    google_oauth: GoogleOAuthService = Depends(get_google_oauth),
    broker: RealtimeBroker = Depends(get_broker),
) -> dict:
    try:
        payload = google_oauth.verify_state(state)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    room_id = payload["room_id"]
    room = await session.get(Room, room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="room not found")

    token_response = await google_oauth.exchange_code(code)
    await google_oauth.store_tokens(session, room_id, token_response)
    session.add(
        AuditLog(
            room_id=room_id,
            actor=payload.get("user_email", ""),
            action="gdrive_connected",
        )
    )
    await session.commit()
    await broker.publish(room_id, {"type": "drive_connected", "room_id": room_id})
    return {"status": "connected", "room_id": room_id}


@router.post(
    "/api/rooms/{room_id}/gdrive/folder", response_model=GDriveStatusOut
)
async def gdrive_link_folder(
    room_id: str,
    payload: GDriveFolderLink,
    session: AsyncSession = Depends(get_session),
    broker: RealtimeBroker = Depends(get_broker),
    user_email: str = Depends(get_current_user_email),
) -> GDriveStatusOut:
    conn = await _get_connection(session, room_id)
    if conn is None or conn.status not in ("connected", "linked"):
        raise HTTPException(
            status_code=409, detail="google drive is not connected for this room"
        )

    conn.google_folder_id = payload.folder_id
    conn.google_folder_name = payload.folder_name
    conn.status = "linked"
    session.add(
        AuditLog(
            room_id=room_id,
            actor=user_email,
            action="gdrive_folder_linked",
            detail={"folder_id": payload.folder_id},
        )
    )
    await session.commit()
    await broker.publish(
        room_id,
        {
            "type": "drive_linked",
            "room_id": room_id,
            "folder_id": payload.folder_id,
            "folder_name": payload.folder_name,
        },
    )
    return _status_out(conn)


@router.get("/api/rooms/{room_id}/gdrive/status", response_model=GDriveStatusOut)
async def gdrive_status(
    room_id: str, session: AsyncSession = Depends(get_session)
) -> GDriveStatusOut:
    conn = await _get_connection(session, room_id)
    if conn is None:
        return GDriveStatusOut(status="none")
    return _status_out(conn)


@router.delete("/api/rooms/{room_id}/gdrive", response_model=GDriveStatusOut)
async def gdrive_revoke(
    room_id: str,
    session: AsyncSession = Depends(get_session),
    user_email: str = Depends(get_current_user_email),
) -> GDriveStatusOut:
    conn = await _get_connection(session, room_id)
    if conn is None:
        raise HTTPException(status_code=404, detail="no google drive connection")

    conn.access_token_enc = None
    conn.refresh_token_enc = None
    conn.token_expiry = None
    conn.status = "revoked"
    session.add(AuditLog(room_id=room_id, actor=user_email, action="gdrive_revoked"))
    await session.commit()
    return _status_out(conn)
