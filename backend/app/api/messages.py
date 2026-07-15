"""Messages API: history, human posts (drive the agents), resume-after-pause."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.orchestrator import ACTIVE, PAUSED, Orchestrator
from ..db.base import get_session
from ..db.models import Message, Room
from ..schemas import (
    MessageCreate,
    MessageEdit,
    MessageEditResult,
    MessageOut,
    PostMessageResult,
)
from .deps import get_orchestrator, require_room_member

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
    _member: str = Depends(require_room_member),
) -> list[MessageOut]:
    # Newest N, returned in chronological order — a long room never hides
    # its most recent messages behind the limit.
    result = await session.execute(
        select(Message)
        .where(Message.room_id == room_id)
        .order_by(Message.seq.desc(), Message.id.desc())
        .limit(limit)
    )
    return _message_out(list(reversed(result.scalars().all())))


@router.post("/messages", response_model=PostMessageResult)
async def post_message(
    room_id: str,
    payload: MessageCreate,
    session: AsyncSession = Depends(get_session),
    orchestrator: Orchestrator = Depends(get_orchestrator),
    user_email: str = Depends(require_room_member),
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


@router.post("/messages/{message_id}/edit", response_model=MessageEditResult)
async def edit_message(
    room_id: str,
    message_id: str,
    payload: MessageEdit,
    request: Request,
    session: AsyncSession = Depends(get_session),
    orchestrator: Orchestrator = Depends(get_orchestrator),
    user_email: str = Depends(require_room_member),
) -> MessageEditResult:
    room = await _get_room(session, room_id)
    target = await session.get(Message, message_id)
    if target is None or target.room_id != room_id:
        raise HTTPException(status_code=404, detail="message not found")
    if target.sender_type != "human" or target.sender_name != user_email:
        raise HTTPException(
            status_code=403, detail="you can only edit your own human messages"
        )
    if target.superseded_at is not None:
        raise HTTPException(
            status_code=409, detail="message already superseded by a later edit"
        )

    result = await orchestrator.handle_message_edit(
        session,
        room,
        target_id=message_id,
        sender_name=user_email,
        content=payload.content,
    )
    if result is None:
        raise HTTPException(
            status_code=409,
            detail="message is no longer the latest editable turn",
        )

    created, superseded_ids = result
    replacement = next((msg for msg in created if msg.sender_type == "human"), None)
    await request.app.state.broker.publish(
        room_id,
        {
            "type": "message_edited",
            "room_id": room_id,
            "message_id": message_id,
            "replacement_message_id": replacement.id if replacement else None,
            "superseded_message_ids": superseded_ids,
        },
    )
    await session.refresh(room)
    return MessageEditResult(
        messages=_message_out(created),
        room_status=room.status,
        cycles_used=room.cycles_used,
        cycle_limit=room.cycle_limit,
        superseded_message_ids=superseded_ids,
    )


@router.post("/resume", response_model=PostMessageResult)
async def resume_room(
    room_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    orchestrator: Orchestrator = Depends(get_orchestrator),
    _member: str = Depends(require_room_member),
) -> PostMessageResult:
    room = await _get_room(session, room_id)

    async with orchestrator.room_lock(room_id):
        # Cross-replica defense-in-depth for the claim below (no-op on
        # SQLite/tests) — see Orchestrator.acquire_replica_lock. PR #18 wired
        # this into handle_human_message but missed it here, leaving resume
        # with zero cross-replica protection even though the in-process
        # room_lock above only serializes callers within this one process.
        await orchestrator.acquire_replica_lock(session, room_id)

        # Atomic paused→active transition: exactly one of any number of
        # concurrent resume clicks wins the fresh budget; the rest get 409.
        result = await session.execute(
            update(Room)
            .where(Room.id == room_id, Room.status == PAUSED)
            .values(status=ACTIVE, cycles_used=0)
            .returning(Room.id)
        )
        claimed = result.scalar_one_or_none()
        await session.commit()
        if claimed is None:
            raise HTTPException(
                status_code=409, detail="room is not paused awaiting a human"
            )
        await session.refresh(room)
        await request.app.state.broker.publish(
            room_id, {"type": "room_resumed", "room_id": room_id}
        )

        created = await orchestrator.run_autonomous_loop(session, room)
        await session.refresh(room)
        return PostMessageResult(
            messages=_message_out(created),
            room_status=room.status,
            cycles_used=room.cycles_used,
            cycle_limit=room.cycle_limit,
        )
