"""Skills API: runtime .md/.zip skill uploads per agent (room-scoped)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.orchestrator import RealtimeBroker
from ..agents.profiles import AGENT_KEYS
from ..db.base import get_session
from ..db.models import AgentSkill, AuditLog
from ..schemas import SkillOut, SkillToggle
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


@router.patch(
    "/api/rooms/{room_id}/agents/{agent_key}/skills/{skill_id}",
    response_model=SkillOut,
)
async def toggle_skill(
    room_id: str,
    agent_key: str,
    skill_id: str,
    payload: SkillToggle,
    session: AsyncSession = Depends(get_session),
    broker: RealtimeBroker = Depends(get_broker),
    user_email: str = Depends(require_room_member),
) -> SkillOut:
    """Enable/disable a room skill for prompt compilation.

    Global skills (room_id NULL) affect every room, so they can only be
    toggled through the admin API — not from inside one room.
    """
    skill = await session.get(AgentSkill, skill_id)
    if skill is None or skill.agent_key != agent_key or (
        skill.room_id is not None and skill.room_id != room_id
    ):
        raise HTTPException(status_code=404, detail="skill not found")
    if skill.room_id is None:
        raise HTTPException(
            status_code=403,
            detail="global skills are admin-managed — toggle via /api/admin",
        )

    skill.enabled = payload.enabled
    session.add(
        AuditLog(
            room_id=room_id,
            actor=user_email,
            action="skill_toggled",
            detail={"skill_id": skill.id, "enabled": payload.enabled},
        )
    )
    await session.commit()
    await broker.publish(
        room_id,
        {
            "type": "skill_toggled",
            "room_id": room_id,
            "agent_key": agent_key,
            "skill_id": skill.id,
            "skill_name": skill.skill_name,
            "enabled": skill.enabled,
        },
    )
    return SkillOut.model_validate(skill, from_attributes=True)
