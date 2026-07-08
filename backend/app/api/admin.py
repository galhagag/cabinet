"""Admin API: read/update the global baseline prompts (audit-logged)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.base import get_session
from ..db.models import AgentGlobalConfig, AuditLog
from ..schemas import AgentConfigOut, AgentConfigUpdate
from .deps import get_current_user_email

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/agents", response_model=list[AgentConfigOut])
async def list_agent_configs(
    session: AsyncSession = Depends(get_session),
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
    agent_key: str, session: AsyncSession = Depends(get_session)
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
    user_email: str = Depends(get_current_user_email),
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
