"""Admin API: global baseline prompts + global agent skills (audit-logged).

Mutations are gated by ``require_admin`` (CABINET_ADMIN_EMAILS allowlist;
empty ⇒ open, development only).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.profiles import AGENT_KEYS
from ..config import get_settings
from ..db.base import get_session
from ..db.models import AgentGlobalConfig, AgentSkill, AuditLog
from ..schemas import AgentConfigOut, AgentConfigUpdate, SkillOut
from ..services.skills import SkillValidationError, SkillsService
from .deps import get_skills_service, require_admin, user_rate_limit

router = APIRouter(prefix="/api/admin", tags=["admin"])
_UPLOAD_READ_CHUNK_SIZE = 64 * 1024


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


@router.get("/agents", response_model=list[AgentConfigOut])
async def list_agent_configs(
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(require_admin),
) -> list[AgentConfigOut]:
    result = await session.execute(
        select(AgentGlobalConfig).order_by(AgentGlobalConfig.agent_key)
    )
    return [
        AgentConfigOut.model_validate(config, from_attributes=True)
        for config in result.scalars().all()
    ]


@router.get("/agents/{agent_key}", response_model=AgentConfigOut)
async def get_agent_config(
    agent_key: str,
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(require_admin),
) -> AgentConfigOut:
    config = await session.get(AgentGlobalConfig, agent_key)
    if config is None:
        raise HTTPException(status_code=404, detail=f"unknown agent: {agent_key}")
    return AgentConfigOut.model_validate(config, from_attributes=True)


@router.put("/agents/{agent_key}", response_model=AgentConfigOut)
async def update_agent_config(
    agent_key: str,
    payload: AgentConfigUpdate,
    session: AsyncSession = Depends(get_session),
    user_email: str = Depends(require_admin),
) -> AgentConfigOut:
    config = await session.get(AgentGlobalConfig, agent_key)
    if config is None:
        raise HTTPException(status_code=404, detail=f"unknown agent: {agent_key}")

    config.system_prompt = payload.system_prompt
    session.add(
        AuditLog(
            actor=user_email,
            action="global_prompt_updated",
            detail={"agent_key": agent_key},
        )
    )
    await session.commit()
    return AgentConfigOut.model_validate(config, from_attributes=True)


@router.post(
    "/agents/{agent_key}/skills", status_code=201, response_model=SkillOut
)
async def upload_global_skill(
    agent_key: str,
    file: UploadFile,
    _rate_limited: None = user_rate_limit(
        scope="global_skill_upload",
        limit_attr="ratelimit_skill_upload_limit",
        window_attr="ratelimit_skill_upload_window",
    ),
    session: AsyncSession = Depends(get_session),
    skills_service: SkillsService = Depends(get_skills_service),
    user_email: str = Depends(require_admin),
) -> SkillOut:
    """Global skill (room_id NULL): applied to this agent in every room."""
    if agent_key not in AGENT_KEYS:
        raise HTTPException(status_code=400, detail=f"unknown agent: {agent_key}")
    filename = file.filename or "upload"
    data = await _read_upload_limited(file, max_bytes=_upload_limit_for(filename))
    try:
        skill = await skills_service.ingest(
            session,
            room_id=None,
            agent_key=agent_key,
            filename=filename,
            data=data,
            actor=user_email,
        )
    except SkillValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SkillOut.model_validate(skill, from_attributes=True)


@router.get("/agents/{agent_key}/skills", response_model=list[SkillOut])
async def list_global_skills(
    agent_key: str,
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(require_admin),
) -> list[SkillOut]:
    result = await session.execute(
        select(AgentSkill)
        .where(AgentSkill.agent_key == agent_key, AgentSkill.room_id.is_(None))
        .order_by(AgentSkill.created_at)
    )
    return [
        SkillOut.model_validate(skill, from_attributes=True)
        for skill in result.scalars().all()
    ]


@router.delete("/agents/{agent_key}/skills/{skill_id}", status_code=204)
async def delete_global_skill(
    agent_key: str,
    skill_id: str,
    session: AsyncSession = Depends(get_session),
    skills_service: SkillsService = Depends(get_skills_service),
    user_email: str = Depends(require_admin),
) -> None:
    skill = await session.get(AgentSkill, skill_id)
    if skill is None or skill.agent_key != agent_key or skill.room_id is not None:
        raise HTTPException(status_code=404, detail="global skill not found")
    await skills_service.delete(session, skill=skill, actor=user_email)
