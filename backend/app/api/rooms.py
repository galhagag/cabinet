"""Rooms API: lifecycle, invites, membership, compiled-prompt inspection."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from ..agents.orchestrator import Orchestrator, RealtimeBroker
from ..agents.profiles import AGENT_KEYS, DISPLAY_NAMES
from ..config import get_settings
from ..db.base import get_session
from ..db.models import AuditLog, Message, Room, RoomAgent, RoomInvite, RoomMember
from ..schemas import (
    CompiledPromptOut,
    InviteCreateOut,
    JoinRequest,
    RealtimeTokenOut,
    RoomAgentOut,
    RoomCreate,
    RoomLastMessageOut,
    RoomMemberOut,
    RoomOut,
)
from .deps import get_current_user_email, get_broker, get_orchestrator, require_room_member

router = APIRouter(prefix="/api/rooms", tags=["rooms"])


def _room_out(
    room: Room, *, last_message: Message | None = None, member_count: int = 0
) -> RoomOut:
    return RoomOut(
        id=room.id,
        customer_name=room.customer_name,
        enrichment_prompt=room.enrichment_prompt,
        status=room.status,
        cycles_used=room.cycles_used,
        cycle_limit=room.cycle_limit,
        created_at=room.created_at,
        agents=[
            RoomAgentOut(agent_key=a.agent_key, display_name=a.display_name)
            for a in room.agents
        ],
        member_count=member_count,
        last_message=(
            RoomLastMessageOut(
                sender_type=last_message.sender_type,
                sender_name=last_message.sender_name,
                agent_key=last_message.agent_key,
                content=last_message.content,
                created_at=last_message.created_at,
            )
            if last_message is not None
            else None
        ),
    )


async def _get_room_with_agents(session: AsyncSession, room_id: str) -> Room:
    room = await session.get(Room, room_id, options=[selectinload(Room.agents)])
    if room is None:
        raise HTTPException(status_code=404, detail="room not found")
    return room


async def _last_messages_by_room(
    session: AsyncSession, room_ids: list[str]
) -> dict[str, Message]:
    """Latest message per room — sidebar preview, one query for every room."""
    if not room_ids:
        return {}
    ranked = (
        select(
            Message,
            func.row_number()
            .over(
                partition_by=Message.room_id,
                order_by=(Message.seq.desc(), Message.id.desc()),
            )
            .label("rn"),
        )
        .where(Message.room_id.in_(room_ids))
        .subquery()
    )
    latest = aliased(Message, ranked)
    result = await session.execute(select(latest).where(ranked.c.rn == 1))
    return {m.room_id: m for m in result.scalars().all()}


async def _member_counts_by_room(
    session: AsyncSession, room_ids: list[str]
) -> dict[str, int]:
    if not room_ids:
        return {}
    result = await session.execute(
        select(RoomMember.room_id, func.count(RoomMember.id))
        .where(RoomMember.room_id.in_(room_ids))
        .group_by(RoomMember.room_id)
    )
    return dict(result.all())


@router.post("", status_code=201, response_model=RoomOut)
async def create_room(
    payload: RoomCreate,
    session: AsyncSession = Depends(get_session),
    user_email: str = Depends(get_current_user_email),
) -> RoomOut:
    existing = await session.execute(
        select(Room.id).where(Room.customer_name == payload.customer_name)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail=f"room for customer already exists: {payload.customer_name}",
        )

    settings = get_settings()
    room = Room(
        customer_name=payload.customer_name,
        enrichment_prompt=payload.enrichment_prompt,
        cycle_limit=settings.default_cycle_limit,
        created_by=user_email,
    )
    room.agents = [
        RoomAgent(agent_key=key, display_name=DISPLAY_NAMES[key])
        for key in AGENT_KEYS
    ]
    room.members = [
        RoomMember(user_email=user_email, display_name=user_email, role="owner")
    ]
    session.add(room)
    await session.flush()
    session.add(
        AuditLog(
            room_id=room.id,
            actor=user_email,
            action="room_created",
            detail={"customer_name": room.customer_name},
        )
    )
    await session.commit()
    return _room_out(room, member_count=len(room.members))


@router.get("", response_model=list[RoomOut])
async def list_rooms(
    session: AsyncSession = Depends(get_session),
    user_email: str = Depends(get_current_user_email),
) -> list[RoomOut]:
    result = await session.execute(
        select(Room)
        .join(RoomMember, RoomMember.room_id == Room.id)
        .where(RoomMember.user_email == user_email)
        .options(selectinload(Room.agents))
        .order_by(Room.created_at)
    )
    rooms = list(result.scalars().all())
    room_ids = [r.id for r in rooms]
    last_messages = await _last_messages_by_room(session, room_ids)
    member_counts = await _member_counts_by_room(session, room_ids)
    return [
        _room_out(
            room,
            last_message=last_messages.get(room.id),
            member_count=member_counts.get(room.id, 0),
        )
        for room in rooms
    ]


@router.post("/join", response_model=RoomOut)
async def join_room(
    payload: JoinRequest,
    session: AsyncSession = Depends(get_session),
    user_email: str = Depends(get_current_user_email),
) -> RoomOut:
    invite = await session.get(RoomInvite, payload.token)
    if invite is None:
        raise HTTPException(status_code=404, detail="unknown invite token")

    expires_at = invite.expires_at
    if expires_at.tzinfo is None:  # SQLite drops tzinfo — stored values are UTC
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="invite expired")

    room = await _get_room_with_agents(session, invite.room_id)

    membership = await session.execute(
        select(RoomMember).where(
            RoomMember.room_id == room.id, RoomMember.user_email == user_email
        )
    )
    if membership.scalar_one_or_none() is None:
        session.add(
            RoomMember(
                room_id=room.id,
                user_email=user_email,
                display_name=payload.display_name or user_email,
                role="member",
            )
        )
        session.add(
            AuditLog(
                room_id=room.id,
                actor=user_email,
                action="member_joined",
                detail={"invite_token": payload.token},
            )
        )
        await session.commit()
    last_messages = await _last_messages_by_room(session, [room.id])
    member_counts = await _member_counts_by_room(session, [room.id])
    return _room_out(
        room,
        last_message=last_messages.get(room.id),
        member_count=member_counts.get(room.id, 0),
    )


@router.get("/{room_id}", response_model=RoomOut)
async def get_room(
    room_id: str,
    session: AsyncSession = Depends(get_session),
    _member: str = Depends(require_room_member),
) -> RoomOut:
    room = await _get_room_with_agents(session, room_id)
    last_messages = await _last_messages_by_room(session, [room.id])
    member_counts = await _member_counts_by_room(session, [room.id])
    return _room_out(
        room,
        last_message=last_messages.get(room.id),
        member_count=member_counts.get(room.id, 0),
    )


@router.get("/{room_id}/members", response_model=list[RoomMemberOut])
async def list_members(
    room_id: str,
    session: AsyncSession = Depends(get_session),
    _member: str = Depends(require_room_member),
) -> list[RoomMemberOut]:
    result = await session.execute(
        select(RoomMember)
        .where(RoomMember.room_id == room_id)
        .order_by(RoomMember.joined_at)
    )
    return [
        RoomMemberOut.model_validate(member, from_attributes=True)
        for member in result.scalars().all()
    ]


@router.post("/{room_id}/invites", status_code=201, response_model=InviteCreateOut)
async def create_invite(
    room_id: str,
    session: AsyncSession = Depends(get_session),
    user_email: str = Depends(require_room_member),
) -> InviteCreateOut:
    room = await session.get(Room, room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="room not found")

    settings = get_settings()
    invite = RoomInvite(
        room_id=room.id,
        created_by=user_email,
        expires_at=datetime.now(timezone.utc)
        + timedelta(hours=settings.invite_ttl_hours),
    )
    session.add(invite)
    session.add(
        AuditLog(room_id=room.id, actor=user_email, action="invite_created")
    )
    await session.commit()
    return InviteCreateOut(
        token=invite.token,
        room_id=room.id,
        expires_at=invite.expires_at,
        join_url=f"/join?token={invite.token}",
    )


@router.get(
    "/{room_id}/agents/{agent_key}/compiled-prompt",
    response_model=CompiledPromptOut,
)
async def get_compiled_prompt(
    room_id: str,
    agent_key: str,
    session: AsyncSession = Depends(get_session),
    orchestrator: Orchestrator = Depends(get_orchestrator),
    _member: str = Depends(require_room_member),
) -> CompiledPromptOut:
    room = await session.get(Room, room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="room not found")
    try:
        compiled = await orchestrator.compiled_prompt(session, room, agent_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CompiledPromptOut(agent_key=agent_key, compiled_prompt=compiled)


@router.get("/{room_id}/realtime-token", response_model=RealtimeTokenOut)
async def realtime_token(
    room_id: str,
    broker: RealtimeBroker = Depends(get_broker),
    user_email: str = Depends(require_room_member),
) -> RealtimeTokenOut:
    result = await broker.client_access(room_id, user_email)
    return RealtimeTokenOut(**result)
