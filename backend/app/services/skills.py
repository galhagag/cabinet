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
_ZIP_READ_CHUNK_SIZE = 64 * 1024
_ZIP_MAX_ENTRIES = 256
_ZIP_MAX_COMPRESSION_RATIO = 100


class SkillValidationError(ValueError):
    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.status_code = status_code


def _first_h1(markdown: str) -> str | None:
    match = _H1_RE.search(markdown)
    return match.group(1) if match else None


def _safe_filename(filename: str) -> str:
    return _UNSAFE_RE.sub("_", PurePosixPath(filename).name) or "upload"


def _decode_utf8(data: bytes, *, label: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillValidationError(f"{label} must be valid UTF-8 text") from exc


def _read_zip_member(
    archive: zipfile.ZipFile, member: zipfile.ZipInfo, *, max_bytes: int
) -> bytes:
    data = bytearray()
    with archive.open(member) as handle:
        while True:
            chunk = handle.read(_ZIP_READ_CHUNK_SIZE)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > max_bytes:
                raise SkillValidationError(
                    f"{PurePosixPath(member.filename).name} exceeds {max_bytes} byte limit",
                    status_code=413,
                )
    return bytes(data)


def _skill_md_from_zip(
    data: bytes,
    *,
    max_skill_md_bytes: int,
    max_total_uncompressed_bytes: int,
) -> str:
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise SkillValidationError(
            "file has .zip extension but is not a valid ZIP archive"
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SkillValidationError(f"invalid zip file: {exc}") from exc
    with archive:
        members = [info for info in archive.infolist() if not info.is_dir()]
        if len(members) > _ZIP_MAX_ENTRIES:
            raise SkillValidationError(
                f"zip skill bundle contains too many files (max {_ZIP_MAX_ENTRIES})",
                status_code=422,
            )

        total_uncompressed = 0
        candidates: list[zipfile.ZipInfo] = []
        for info in members:
            member_name = PurePosixPath(info.filename).name.lower()
            total_uncompressed += info.file_size
            if total_uncompressed > max_total_uncompressed_bytes:
                raise SkillValidationError(
                    "zip skill bundle expands beyond the allowed uncompressed size",
                    status_code=413,
                )
            if member_name == "skill.md" and info.file_size > max_skill_md_bytes:
                raise SkillValidationError(
                    f"SKILL.md exceeds {max_skill_md_bytes} byte limit",
                    status_code=413,
                )
            if info.compress_size > 0:
                ratio = info.file_size / info.compress_size
                if ratio > _ZIP_MAX_COMPRESSION_RATIO:
                    raise SkillValidationError(
                        "zip skill bundle compression ratio exceeds the allowed limit",
                        status_code=422,
                    )
            if member_name == "skill.md":
                candidates.append(info)

        if not candidates:
            raise SkillValidationError("zip skill bundle must contain SKILL.md")
        # Prefer the shallowest SKILL.md (bundle root over nested copies).
        member = min(candidates, key=lambda info: len(PurePosixPath(info.filename).parts))
        member_data = _read_zip_member(
            archive, member, max_bytes=max_skill_md_bytes
        )
        return _decode_utf8(member_data, label="SKILL.md")


class SkillsService:
    def __init__(
        self,
        blob: BlobStorageProvider,
        *,
        md_max_bytes: int = 1_048_576,
        zip_max_bytes: int = 5_242_880,
        zip_total_uncompressed_max_bytes: int = 10_485_760,
    ) -> None:
        self._blob = blob
        self._md_max_bytes = md_max_bytes
        self._zip_max_bytes = zip_max_bytes
        self._zip_total_uncompressed_max_bytes = zip_total_uncompressed_max_bytes

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
            if len(data) > self._md_max_bytes:
                raise SkillValidationError(
                    f"skill markdown exceeds {self._md_max_bytes} byte limit",
                    status_code=413,
                )
            skill_type = "md"
            content_text = _decode_utf8(data, label="skill markdown")
            skill_name = _first_h1(content_text) or stem
        elif lowered.endswith(".zip"):
            if len(data) > self._zip_max_bytes:
                raise SkillValidationError(
                    f"skill zip exceeds {self._zip_max_bytes} byte limit",
                    status_code=413,
                )
            skill_type = "zip"
            content_text = _skill_md_from_zip(
                data,
                max_skill_md_bytes=self._md_max_bytes,
                max_total_uncompressed_bytes=self._zip_total_uncompressed_max_bytes,
            )
            skill_name = _first_h1(content_text) or stem
        else:
            raise SkillValidationError("unsupported skill file type")

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

    async def delete(
        self,
        session: AsyncSession,
        *,
        skill: AgentSkill,
        actor: str = "system",
    ) -> None:
        """Remove a global skill: its blob, its DB row, and an audit entry."""
        await self._blob.delete(skill.blob_path)
        session.add(
            AuditLog(
                room_id=skill.room_id,
                actor=actor,
                action="global_skill_deleted",
                detail={
                    "agent_key": skill.agent_key,
                    "skill_id": skill.id,
                    "skill_name": skill.skill_name,
                },
            )
        )
        await session.delete(skill)
        await session.commit()
