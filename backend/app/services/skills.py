"""Runtime skill ingestion: .md documents and .zip bundles.

The markdown body (for bundles: the SKILL.md member) becomes the
``content_text`` the prompt compiler appends to the agent's system prompt;
the raw upload is persisted to blob storage for provenance.
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import PurePosixPath
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AgentSkill, AuditLog
from .blob_storage import BlobStorageProvider

_H1_RE = re.compile(r"^#[ \t]+(.+?)\s*$", re.MULTILINE)
_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


def _first_h1(markdown: str) -> str | None:
    match = _H1_RE.search(markdown)
    return match.group(1) if match else None


def _safe_filename(filename: str) -> str:
    return _UNSAFE_RE.sub("_", PurePosixPath(filename).name) or "upload"


def _skill_md_from_zip(data: bytes) -> str:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"invalid zip file: {exc}") from exc
    with archive:
        candidates = [
            name
            for name in archive.namelist()
            if PurePosixPath(name).name.lower() == "skill.md"
        ]
        if not candidates:
            raise ValueError("zip skill bundle must contain SKILL.md")
        # Prefer the shallowest SKILL.md (bundle root over nested copies).
        member = min(candidates, key=lambda name: len(PurePosixPath(name).parts))
        return archive.read(member).decode("utf-8")


class SkillsService:
    def __init__(self, blob: BlobStorageProvider) -> None:
        self._blob = blob

    async def ingest(
        self,
        session: AsyncSession,
        *,
        room_id: str | None,
        agent_key: str,
        filename: str,
        data: bytes,
        actor: str = "system",
    ) -> AgentSkill:
        """Validate, store, and register a skill upload for one agent."""
        stem = PurePosixPath(filename).stem
        lowered = filename.lower()

        if lowered.endswith(".md"):
            skill_type = "md"
            content_text = data.decode("utf-8")
            skill_name = _first_h1(content_text) or stem
        elif lowered.endswith(".zip"):
            skill_type = "zip"
            content_text = _skill_md_from_zip(data)
            skill_name = _first_h1(content_text) or stem
        else:
            raise ValueError("unsupported skill file type")

        blob_path = (
            f"skills/{room_id or 'global'}/{agent_key}/"
            f"{uuid4()}-{_safe_filename(filename)}"
        )
        await self._blob.upload(blob_path, data)

        skill = AgentSkill(
            room_id=room_id,
            agent_key=agent_key,
            skill_name=skill_name,
            skill_type=skill_type,
            blob_path=blob_path,
            content_text=content_text,
        )
        session.add(skill)
        session.add(
            AuditLog(
                room_id=room_id,
                actor=actor,
                action="skill_uploaded",
                detail={
                    "agent_key": agent_key,
                    "skill_name": skill_name,
                    "skill_type": skill_type,
                    "blob_path": blob_path,
                },
            )
        )
        await session.commit()
        return skill
