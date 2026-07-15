"""Shared FastAPI dependencies: caller identity, authorization, singletons."""
from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.orchestrator import Orchestrator, RealtimeBroker
from ..config import get_settings
from ..db.base import get_session
from ..db.models import Room, RoomMember
from ..services.entra_auth import EntraTokenError, EntraTokenValidator
from ..services.google_oauth import GoogleOAuthService
from ..services.ratelimit import RateLimiter
from ..services.realtime import ConnectionManager
from ..services.skills import SkillsService

DEFAULT_DEV_EMAIL = "dev@thetaray.com"

# auto_error=False: in "dev" auth mode there may be no bearer token at all,
# and we want to fall through to the X-User-Email header rather than 403.
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user_email(
    request: Request,
    x_user_email: str = Header(default=DEFAULT_DEV_EMAIL),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """Caller identity.

    ``CABINET_AUTH_MODE=dev`` (default): the ``X-User-Email`` header, trusted
    as-is — dev/test only, never set in production.

    ``CABINET_AUTH_MODE=entra``: the ``Authorization: Bearer`` access token is
    verified against the tenant's Entra ID JWKS (signature, issuer, audience,
    expiry); the caller's email comes from the token's verified claims, never
    from a client-supplied header. Everything downstream (membership checks,
    admin allowlist) is unchanged either way.
    """
    settings = get_settings()
    if settings.auth_mode != "entra":
        return x_user_email

    if credentials is None:
        raise HTTPException(status_code=401, detail="missing bearer token")
    validator: EntraTokenValidator = request.app.state.entra_validator
    try:
        return await validator.validate(credentials.credentials)
    except EntraTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


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

    An empty allowlist means open access in "dev" auth mode only — an empty
    allowlist under "entra" auth is refused outright, so a forgotten
    CABINET_ADMIN_EMAILS can never open the admin surface to every verified
    user (defense in depth alongside the boot guard in config.py).
    """
    settings = get_settings()
    allowlist = {
        e.strip().lower() for e in settings.admin_emails.split(",") if e.strip()
    }
    if settings.auth_mode == "entra" and not allowlist:
        raise HTTPException(status_code=403, detail="admin access required")
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


def _enforce_rate_limit(
    request: Request,
    *,
    key: str,
    scope: str,
    limit_attr: str,
    window_attr: str,
) -> None:
    settings = request.app.state.settings
    limiter: RateLimiter = request.app.state.rate_limiter
    limit = getattr(settings, limit_attr)
    window = getattr(settings, window_attr)
    decision = limiter.acquire(key=key, limit=limit, window_seconds=window)
    if decision.allowed:
        return
    raise HTTPException(
        status_code=429,
        detail=f"{scope} rate limit exceeded; retry in {decision.retry_after}s",
        headers={"Retry-After": str(decision.retry_after)},
    )


def user_rate_limit(
    *,
    scope: str,
    limit_attr: str,
    window_attr: str,
    key_builder: Callable[[str], str] | None = None,
):
    async def dependency(
        request: Request,
        user_email: str = Depends(get_current_user_email),
    ) -> None:
        normalized = user_email.lower()
        key = key_builder(normalized) if key_builder is not None else normalized
        _enforce_rate_limit(
            request,
            key=f"{scope}:{key}",
            scope=scope,
            limit_attr=limit_attr,
            window_attr=window_attr,
        )

    return Depends(dependency)


def room_rate_limit(*, scope: str, limit_attr: str, window_attr: str):
    async def dependency(
        request: Request,
        room_id: str,
        user_email: str = Depends(require_room_member),
    ) -> None:
        _enforce_rate_limit(
            request,
            key=f"{scope}:{user_email.lower()}:{room_id}",
            scope=scope,
            limit_attr=limit_attr,
            window_attr=window_attr,
        )

    return Depends(dependency)
