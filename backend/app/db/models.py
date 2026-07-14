"""ORM models — see docs/ARCHITECTURE.md §4 for the schema diagram.

Every table runs unchanged on Azure Database for PostgreSQL (Flexible
Server, prod) and SQLite (tests). Messages and audit_log rows are
immutable and form the regulatory audit trail.
"""
from __future__ import annotations

import secrets
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_invite_token() -> str:
    return secrets.token_urlsafe(32)


class AgentGlobalConfig(Base):
    """Global baseline system prompts, editable by platform admins."""

    __tablename__ = "agent_global_config"

    agent_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128))
    system_prompt: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class Room(Base):
    __tablename__ = "rooms"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'paused_awaiting_human')", name="ck_rooms_status"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    customer_name: Mapped[str] = mapped_column(String(256), unique=True)
    enrichment_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "active" | "paused_awaiting_human"
    status: Mapped[str] = mapped_column(String(32), default="active")
    cycles_used: Mapped[int] = mapped_column(Integer, default=0)
    cycle_limit: Mapped[int] = mapped_column(Integer, default=6)
    created_by: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    agents: Mapped[list["RoomAgent"]] = relationship(
        back_populates="room", cascade="all, delete-orphan"
    )
    members: Mapped[list["RoomMember"]] = relationship(
        back_populates="room", cascade="all, delete-orphan"
    )


class RoomAgent(Base):
    __tablename__ = "room_agents"
    __table_args__ = (UniqueConstraint("room_id", "agent_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"))
    agent_key: Mapped[str] = mapped_column(String(32))
    display_name: Mapped[str] = mapped_column(String(128))
    instructions: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    room: Mapped[Room] = relationship(back_populates="agents")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_room_seq", "room_id", "seq"),
        CheckConstraint(
            "sender_type IN ('human', 'agent', 'system')",
            name="ck_messages_sender_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Monotonic ordering key: created_at has only µs resolution and can tie
    # within a burst of agent turns; seq (wall-clock ns) breaks ties
    # deterministically. Ordering is always (seq, id).
    seq: Mapped[int] = mapped_column(BigInteger, default=time.time_ns)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="RESTRICT"))
    # "human" | "agent" | "system"
    sender_type: Mapped[str] = mapped_column(String(16))
    sender_name: Mapped[str] = mapped_column(String(256))
    agent_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mention_target: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cycle_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content: Mapped[str] = mapped_column(Text)
    # Populated for agent replies only — usage reported by the LLM backend.
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # One entry per tool call made while producing this reply, e.g.
    # {"tool": "web_search", "query": "...", "success": True}. None for
    # messages that never used a tool.
    tool_invocations: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RoomMember(Base):
    __tablename__ = "room_members"
    __table_args__ = (
        UniqueConstraint("room_id", "user_email"),
        CheckConstraint("role IN ('owner', 'member')", name="ck_room_members_role"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"))
    user_email: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str] = mapped_column(String(256), default="")
    # "owner" | "member"
    role: Mapped[str] = mapped_column(String(16), default="member")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    room: Mapped[Room] = relationship(back_populates="members")


class RoomInvite(Base):
    __tablename__ = "room_invites"

    token: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=new_invite_token
    )
    room_id: Mapped[str] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[str] = mapped_column(String(256))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class GDriveConnection(Base):
    """Google Drive OAuth2 link for a room.

    Access/refresh tokens are Fernet-encrypted before persistence; the
    encryption key lives in Azure Key Vault (secret: token-encryption-key).
    """

    __tablename__ = "gdrive_connections"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'connected', 'linked', 'error', 'revoked')",
            name="ck_gdrive_connections_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    room_id: Mapped[str] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), unique=True
    )
    google_folder_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    google_folder_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    access_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expiry: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scopes: Mapped[str] = mapped_column(Text, default="")
    # "pending" | "connected" | "linked" | "error" | "revoked"
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class AgentSkill(Base):
    """Runtime-uploaded skill (.md document or .zip bundle).

    room_id NULL ⇒ global skill applied to that agent in every room.
    content_text holds the markdown body (for .zip bundles: SKILL.md), which
    the prompt compiler appends to the agent's system prompt. The raw upload
    is persisted to Azure Blob Storage at blob_path.
    """

    __tablename__ = "agent_skills"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    room_id: Mapped[str | None] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), nullable=True, index=True
    )
    agent_key: Mapped[str] = mapped_column(String(32), index=True)
    skill_name: Mapped[str] = mapped_column(String(256))
    # "md" | "zip"
    skill_type: Mapped[str] = mapped_column(String(8))
    blob_path: Mapped[str] = mapped_column(String(1024))
    content_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RoomSkillOverride(Base):
    """Room-scoped disable toggle for a skill (global or room-owned).

    Row presence means "disabled in this room" — this keeps a global skill's
    on/off state scoped to the room where a member toggled it, since
    AgentSkill.room_id is NULL (shared) for global skills.
    """

    __tablename__ = "room_skill_overrides"

    room_id: Mapped[str] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[str] = mapped_column(
        ForeignKey("agent_skills.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RoomToolOverride(Base):
    """Room-scoped disable toggle for a built-in tool — identical precedent
    to RoomSkillOverride. Tools are code-defined (TOOL_REGISTRY), not DB
    rows; this table only ever records the disabled exception.
    """

    __tablename__ = "room_tool_overrides"

    room_id: Mapped[str] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True
    )
    tool_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[str | None] = mapped_column(
        ForeignKey("rooms.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor: Mapped[str] = mapped_column(String(256))
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
