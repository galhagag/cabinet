"""Skills API: runtime .md/.zip skill uploads per agent (room-scoped)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.orchestrator import RealtimeBroker
from ..agents.profiles import AGENT_KEYS
from ..db.base import get_session
from ..db.models import AgentSkill
from ..schemas import SkillOut
from ..services.skills import SkillsService
from .deps import get_broker, get_skills_service, require_room_member

router = APIRouter(tags=["skills"])


@router.post(
    "/api/rooms/{room_id}/agents/{agent_key}/skills",
    status_code=201,
    response_model=SkillOut,
)
async def upload_skill(
    room_id: str,
    agent_key: str,
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
    skills_service: SkillsService = Depends(get_skills_service),
    broker: RealtimeBroker = Depends(get_broker),
    user_email: str = Depends(require_room_member),
) -> SkillOut:
    if agent_key not in AGENT_KEYS:
        raise HTTPException(status_code=400, detail=f"unknown agent: {agent_key}")

    data = await file.read()
    try:
        skill = await skills_service.ingest(
            session,
            room_id=room_id,
            agent_key=agent_key,
            filename=file.filename or "upload",
            data=data,
            actor=user_email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await broker.publish(
        room_id,
        {
            "type": "skill_added",
            "room_id": room_id,
            "agent_key": agent_key,
            "skill_name": skill.skill_name,
        },
    )
    return SkillOut.model_validate(skill, from_attributes=True)


@router.get(
    "/api/rooms/{room_id}/agents/{agent_key}/skills",
    response_model=list[SkillOut],
)
async def list_skills(
    room_id: str,
    agent_key: str,
    session: AsyncSession = Depends(get_session),
    _member: str = Depends(require_room_member),
) -> list[SkillOut]:
    result = await session.execute(
        select(AgentSkill)
        .where(
            AgentSkill.agent_key == agent_key,
            (AgentSkill.room_id == room_id) | (AgentSkill.room_id.is_(None)),
        )
        .order_by(AgentSkill.created_at)
    )
    return [
        SkillOut.model_validate(skill, from_attributes=True)
        for skill in result.scalars().all()
    ]
