"""Pydantic API contracts shared by all routers."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# --- Admin ---------------------------------------------------------------
class AgentConfigOut(BaseModel):
    agent_key: str
    display_name: str
    system_prompt: str
    updated_at: datetime


class AgentConfigUpdate(BaseModel):
    system_prompt: str = Field(min_length=1)


# --- Rooms ----------------------------------------------------------------
class RoomCreate(BaseModel):
    customer_name: str = Field(min_length=1, max_length=256)
    enrichment_prompt: str | None = None


class RoomAgentOut(BaseModel):
    agent_key: str
    display_name: str


class RoomOut(BaseModel):
    id: str
    customer_name: str
    enrichment_prompt: str | None
    status: str
    cycles_used: int
    cycle_limit: int
    created_at: datetime
    agents: list[RoomAgentOut] = []


class RoomMemberOut(BaseModel):
    user_email: str
    display_name: str
    role: str
    joined_at: datetime


class InviteCreateOut(BaseModel):
    token: str
    room_id: str
    expires_at: datetime
    join_url: str


class JoinRequest(BaseModel):
    token: str
    display_name: str = ""


# --- Messages ---------------------------------------------------------------
class MessageCreate(BaseModel):
    content: str = Field(min_length=1)


class MessageOut(BaseModel):
    id: str
    room_id: str
    sender_type: str
    sender_name: str
    agent_key: str | None
    mention_target: str | None
    cycle_number: int | None
    content: str
    created_at: datetime


class PostMessageResult(BaseModel):
    messages: list[MessageOut]
    room_status: str
    cycles_used: int
    cycle_limit: int


# --- Google Drive -------------------------------------------------------------
class GDriveAuthorizeOut(BaseModel):
    authorize_url: str
    state: str


class GDriveStatusOut(BaseModel):
    status: str
    google_folder_id: str | None = None
    google_folder_name: str | None = None
    token_expiry: datetime | None = None
    scopes: str = ""


class GDriveFolderLink(BaseModel):
    folder_id: str = Field(min_length=1)
    folder_name: str = ""


# --- Skills -----------------------------------------------------------------
class SkillOut(BaseModel):
    id: str
    room_id: str | None
    agent_key: str
    skill_name: str
    skill_type: str
    blob_path: str
    enabled: bool
    created_at: datetime


class SkillToggle(BaseModel):
    enabled: bool


# --- Compiled prompt (debug/inspection) ----------------------------------------
class CompiledPromptOut(BaseModel):
    agent_key: str
    compiled_prompt: str
