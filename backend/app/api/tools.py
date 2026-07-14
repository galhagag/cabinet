"""Tools API: built-in per-agent tool catalog and per-room enable/disable
toggle. Mirrors skills.py exactly — tools are code-defined (TOOL_REGISTRY),
so there is no upload endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.orchestrator import RealtimeBroker
from ..agents.profiles import AGENT_KEYS
from ..agents.tools import TOOL_REGISTRY, ToolDefinition, _room_has_connected_drive
from ..db.base import get_session
from ..db.models import AuditLog, RoomToolOverride
from ..schemas import ToolOut, ToolToggleUpdate
from .deps import get_broker, require_room_member

router = APIRouter(tags=["tools"])


async def _tools_for_agent(
    session: AsyncSession, room_id: str, agent_key: str
) -> list[ToolDefinition]:
    tools = [t for t in TOOL_REGISTRY.values() if agent_key in t.default_agents]
    if any(t.name == "drive_search" for t in tools) and not await _room_has_connected_drive(
        session, room_id
    ):
        tools = [t for t in tools if t.name != "drive_search"]
    return tools


@router.get(
    "/api/rooms/{room_id}/agents/{agent_key}/tools",
    response_model=list[ToolOut],
)
async def list_tools(
    room_id: str,
    agent_key: str,
    session: AsyncSession = Depends(get_session),
    _member: str = Depends(require_room_member),
) -> list[ToolOut]:
    if agent_key not in AGENT_KEYS:
        raise HTTPException(status_code=400, detail=f"unknown agent: {agent_key}")

    overrides = await session.execute(
        select(RoomToolOverride.tool_name).where(RoomToolOverride.room_id == room_id)
    )
    disabled = set(overrides.scalars().all())
    return [
        ToolOut(name=t.name, description=t.description, enabled=t.name not in disabled)
        for t in await _tools_for_agent(session, room_id, agent_key)
    ]


@router.put(
    "/api/rooms/{room_id}/agents/{agent_key}/tools/{tool_name}",
    response_model=ToolOut,
)
async def toggle_tool(
    room_id: str,
    agent_key: str,
    tool_name: str,
    payload: ToolToggleUpdate,
    session: AsyncSession = Depends(get_session),
    broker: RealtimeBroker = Depends(get_broker),
    user_email: str = Depends(require_room_member),
) -> ToolOut:
    if agent_key not in AGENT_KEYS:
        raise HTTPException(status_code=400, detail=f"unknown agent: {agent_key}")
    tool = TOOL_REGISTRY.get(tool_name)
    if tool is None or agent_key not in tool.default_agents:
        raise HTTPException(status_code=404, detail="tool not found")

    existing = await session.get(RoomToolOverride, (room_id, tool_name))
    if payload.enabled and existing is not None:
        await session.delete(existing)
    elif not payload.enabled and existing is None:
        session.add(RoomToolOverride(room_id=room_id, tool_name=tool_name))

    session.add(
        AuditLog(
            room_id=room_id,
            actor=user_email,
            action="room_tool_toggled",
            detail={
                "agent_key": agent_key,
                "tool_name": tool_name,
                "enabled": payload.enabled,
            },
        )
    )
    await session.commit()
    await broker.publish(
        room_id,
        {
            "type": "agent_tool_toggled",
            "room_id": room_id,
            "agent_key": agent_key,
            "tool_name": tool_name,
            "enabled": payload.enabled,
        },
    )
    return ToolOut(name=tool.name, description=tool.description, enabled=payload.enabled)
