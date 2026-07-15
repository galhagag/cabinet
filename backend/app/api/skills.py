"""Skills API: runtime .md/.zip skill uploads per agent (room-scoped)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.orchestrator import RealtimeBroker
from ..agents.profiles import AGENT_KEYS
from ..config import get_settings
from ..db.base import get_session
from ..db.models import AgentSkill, AuditLog, RoomSkillOverride
from ..schemas import SkillOut, SkillToggleUpdate
from ..services.skills import SkillValidationError, SkillsService
from .deps import get_broker, get_skills_service, require_room_member, room_rate_limit

router = APIRouter(tags=["skills"])
_UPLOAD_READ_CHUNK_SIZE = 64 * 1024


def _skill_out(skill: AgentSkill, *, enabled: bool) -> SkillOut:
    return SkillOut(
        id=skill.id,
        room_id=skill.room_id,
        agent_key=skill.agent_key,
        skill_name=skill.skill_name,
        skill_type=skill.skill_type,
        blob_path=skill.blob_path,
        created_at=skill.created_at,
        enabled=enabled,
    )


def _upload_limit_for(filename: str) -> int:
    settings = get_settings()
    lowered = filename.lower()
    if lowered.endswith(".md"):
        return settings.skill_md_max_bytes
    if lowered.endswith(".zip"):
        return settings.skill_zip_max_bytes
    raise HTTPException(status_code=400, detail="unsupported skill file type")


async def _read_upload_limited(file: UploadFile, *, max_bytes: int) -> bytes:
    data = bytearray()
    while True:
        chunk = await file.read(_UPLOAD_READ_CHUNK_SIZE)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"skill upload exceeds {max_bytes} byte limit",
            )
    return bytes(data)


@router.post(
    "/api/rooms/{room_id}/agents/{agent_key}/skills",
    status_code=201,
    response_model=SkillOut,
)
async def upload_skill(
    room_id: str,
    agent_key: str,
    file: UploadFile,
    _rate_limited: None = room_rate_limit(
        scope="skill_upload",
        limit_attr="ratelimit_skill_upload_limit",
        window_attr="ratelimit_skill_upload_window",
    ),
    session: AsyncSession = Depends(get_session),
    skills_service: SkillsService = Depends(get_skills_service),
    broker: RealtimeBroker = Depends(get_broker),
    user_email: str = Depends(require_room_member),
) -> SkillOut:
    if agent_key not in AGENT_KEYS:
        raise HTTPException(status_code=400, detail=f"unknown agent: {agent_key}")

    filename = file.filename or "upload"
    data = await _read_upload_limited(file, max_bytes=_upload_limit_for(filename))
    try:
        skill = await skills_service.ingest(
            session,
            room_id=room_id,
            agent_key=agent_key,
            filename=filename,
            data=data,
            actor=user_email,
        )
    except SkillValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
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
    skills = result.scalars().all()

    overrides = await session.execute(
        select(RoomSkillOverride.skill_id).where(RoomSkillOverride.room_id == room_id)
    )
    disabled_ids = set(overrides.scalars().all())

    return [_skill_out(skill, enabled=skill.id not in disabled_ids) for skill in skills]


@router.put(
    "/api/rooms/{room_id}/agents/{agent_key}/skills/{skill_id}",
    response_model=SkillOut,
)
async def toggle_skill(
    room_id: str,
    agent_key: str,
    skill_id: str,
    payload: SkillToggleUpdate,
    session: AsyncSession = Depends(get_session),
    broker: RealtimeBroker = Depends(get_broker),
    user_email: str = Depends(require_room_member),
) -> SkillOut:
    skill = await session.get(AgentSkill, skill_id)
    if skill is None or skill.agent_key != agent_key:
        raise HTTPException(status_code=404, detail="skill not found")
    if skill.room_id is not None and skill.room_id != room_id:
        raise HTTPException(status_code=404, detail="skill not found")

    existing = await session.get(RoomSkillOverride, (room_id, skill_id))
    if payload.enabled and existing is not None:
        await session.delete(existing)
    elif not payload.enabled and existing is None:
        session.add(RoomSkillOverride(room_id=room_id, skill_id=skill_id))

    session.add(
        AuditLog(
            room_id=room_id,
            actor=user_email,
            action="room_skill_toggled",
            detail={
                "agent_key": agent_key,
                "skill_id": skill_id,
                "enabled": payload.enabled,
            },
        )
    )
    await session.commit()
    await broker.publish(
        room_id,
        {
            "type": "agent_skill_toggled",
            "room_id": room_id,
            "agent_key": agent_key,
            "skill_id": skill_id,
            "enabled": payload.enabled,
        },
    )
    return _skill_out(skill, enabled=payload.enabled)
