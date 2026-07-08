"""Messages API: history, human posts (drive the agents), resume-after-pause."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.orchestrator import ACTIVE, PAUSED, Orchestrator
from ..db.base import get_session
from ..db.models import Message, Room
from ..schemas import MessageCreate, MessageOut, PostMessageResult
from .deps import get_current_user_email, get_orchestrator

router = APIRouter(prefix="/api/rooms/{room_id}", tags=["messages"])


async def _get_room(session: AsyncSession, room_id: str) -> Room:
    room = await session.get(Room, room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="room not found")
    return room


def _message_out(messages: list[Message]) -> list[MessageOut]:
    return [MessageOut.model_validate(m, from_attributes=True) for m in messages]


@router.get("/messages", response_model=list[MessageOut])
async def list_messages(
    room_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> list[MessageOut]:
    await _get_room(session, room_id)
    result = await session.execute(
        select(Message)
        .where(Message.room_id == room_id)
        .order_by(Message.created_at, Message.id)
        .limit(limit)
    )
    return _message_out(list(result.scalars().all()))


@router.post("/messages", response_model=PostMessageResult)
async def post_message(
    room_id: str,
    payload: MessageCreate,
    session: AsyncSession = Depends(get_session),
    orchestrator: Orchestrator = Depends(get_orchestrator),
    user_email: str = Depends(get_current_user_email),
) -> PostMessageResult:
    room = await _get_room(session, room_id)
    created = await orchestrator.handle_human_message(
        session, room, sender_name=user_email, content=payload.content
    )
    await session.refresh(room)
    return PostMessageResult(
        messages=_message_out(created),
        room_status=room.status,
        cycles_used=room.cycles_used,
        cycle_limit=room.cycle_limit,
    )


@router.post("/resume", response_model=PostMessageResult)
async def resume_room(
    room_id: str,
    session: AsyncSession = Depends(get_session),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> PostMessageResult:
    room = await _get_room(session, room_id)
    if room.status != PAUSED:
        raise HTTPException(
            status_code=409, detail="room is not paused awaiting a human"
        )

    room.cycles_used = 0
    room.status = ACTIVE
    await session.commit()

    created = await orchestrator.run_autonomous_loop(session, room)
    await session.refresh(room)
    return PostMessageResult(
        messages=_message_out(created),
        room_status=room.status,
        cycles_used=room.cycles_used,
        cycle_limit=room.cycle_limit,
    )
