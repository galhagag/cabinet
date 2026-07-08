"""Shared FastAPI dependencies: caller identity, authorization, singletons."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.orchestrator import Orchestrator, RealtimeBroker
from ..config import get_settings
from ..db.base import get_session
from ..db.models import Room, RoomMember
from ..services.google_oauth import GoogleOAuthService
from ..services.realtime import ConnectionManager
from ..services.skills import SkillsService

DEFAULT_DEV_EMAIL = "dev@thetaray.com"


def get_current_user_email(
    x_user_email: str = Header(default=DEFAULT_DEV_EMAIL),
) -> str:
    """Caller identity from the ``X-User-Email`` header (dev/test only).

    Production swaps this single dependency for Microsoft Entra ID JWT
    validation (bearer token → verified email claim); everything downstream
    is unchanged.
    """
    return x_user_email


async def is_room_member(
    session: AsyncSession, room_id: str, user_email: str
) -> bool:
    result = await session.execute(
        select(RoomMember.id).where(
            RoomMember.room_id == room_id, RoomMember.user_email == user_email
        )
    )
    return result.scalar_one_or_none() is not None


async def require_room_member(
    room_id: str,
    session: AsyncSession = Depends(get_session),
    user_email: str = Depends(get_current_user_email),
) -> str:
    """Authorize room-scoped access: 404 unknown room, 403 non-member.

    Membership is granted at room creation (owner) or via invite join —
    this is what makes invite links an actual access-control boundary.
    """
    if await session.get(Room, room_id) is None:
        raise HTTPException(status_code=404, detail="room not found")
    if not await is_room_member(session, room_id, user_email):
        raise HTTPException(
            status_code=403, detail="not a member of this room — ask for an invite"
        )
    return user_email


def require_admin(user_email: str = Depends(get_current_user_email)) -> str:
    """Gate platform-admin surfaces behind CABINET_ADMIN_EMAILS.

    An empty allowlist means open access (development). Production must set
    the allowlist or replace this with an Entra ID app-role check.
    """
    allowlist = {
        e.strip().lower()
        for e in get_settings().admin_emails.split(",")
        if e.strip()
    }
    if allowlist and user_email.lower() not in allowlist:
        raise HTTPException(status_code=403, detail="admin access required")
    return user_email


def get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator


def get_google_oauth(request: Request) -> GoogleOAuthService:
    return request.app.state.google_oauth


def get_skills_service(request: Request) -> SkillsService:
    return request.app.state.skills_service


def get_manager(request: Request) -> ConnectionManager:
    return request.app.state.manager


def get_broker(request: Request) -> RealtimeBroker:
    return request.app.state.broker
