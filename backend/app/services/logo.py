"""Room logo: Brandfetch auto-lookup on room creation, plus a manual override
upload. Both paths funnel through blob storage the same way skill uploads do.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db.models import AuditLog, Room
from .blob_storage import BlobStorageProvider
from .secrets import SecretProvider

logger = logging.getLogger(__name__)

# Only these three formats are accepted — an auto-fetched icon in any other
# format (Brandfetch can return SVG) is treated as "no usable logo" rather
# than stored, same restriction the manual upload path enforces.
_CONTENT_TYPE_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
MAX_LOGO_BYTES = 2 * 1024 * 1024


def logo_url_for(room: Room) -> str | None:
    return f"/api/rooms/{room.id}/logo" if room.logo_blob_path else None


class LogoService:
    def __init__(
        self,
        settings: Settings,
        secret_provider: SecretProvider,
        blob: BlobStorageProvider,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._secrets = secret_provider
        self._blob = blob
        self._transport = transport

    # ------------------------------------------------------------------
    # Auto-lookup (background task after room creation)
    # ------------------------------------------------------------------
    async def fetch_for_room(self, session: AsyncSession, room_id: str) -> Room | None:
        """Best-effort Brandfetch lookup. Never raises — any failure or
        no-match lands on logo_source="none", not an exception, since this
        runs unsupervised in a background task with no request to fail."""
        room = await session.get(Room, room_id)
        if room is None:
            return None

        try:
            logo_source = await self._try_fetch(room)
            error: str | None = None
        except Exception as exc:  # noqa: BLE001 — deliberately broad, see docstring
            logger.warning("logo fetch failed for room %s", room_id, exc_info=True)
            logo_source = "none"
            error = str(exc)[:200]

        room.logo_source = logo_source
        detail: dict = {"logo_source": logo_source}
        if error is not None:
            detail["error"] = error
        session.add(
            AuditLog(room_id=room_id, actor="system", action="room_logo_fetched", detail=detail)
        )
        await session.commit()
        return room

    async def _try_fetch(self, room: Room) -> str:
        """Returns the resulting logo_source ("auto" or "none"); raises on
        any request failure so the caller can record it and fall back."""
        client_id = await self._secrets.get_secret(self._settings.brandfetch_client_id_secret)

        async with httpx.AsyncClient(transport=self._transport, timeout=10.0) as client:
            search_resp = await client.get(
                f"{self._settings.brandfetch_search_endpoint}/"
                f"{quote(room.customer_name, safe='')}",
                params={"c": client_id},
            )
            search_resp.raise_for_status()
            matches = search_resp.json()
            icon_url = next(
                (m.get("icon") for m in matches if isinstance(m, dict) and m.get("icon")),
                None,
            )
            if not icon_url:
                return "none"

            image_resp = await client.get(icon_url)

        image_resp.raise_for_status()
        content_type = image_resp.headers.get("content-type", "").split(";")[0].strip()
        ext = _CONTENT_TYPE_EXT.get(content_type)
        if ext is None:
            return "none"

        blob_path = f"rooms/{room.id}/logo.{ext}"
        await self._blob.upload(blob_path, image_resp.content)
        room.logo_blob_path = blob_path
        return "auto"

    # ------------------------------------------------------------------
    # Manual override upload
    # ------------------------------------------------------------------
    async def save_upload(
        self,
        session: AsyncSession,
        room: Room,
        *,
        content_type: str | None,
        data: bytes,
        actor: str,
    ) -> None:
        """Validate and persist a member-uploaded logo onto `room` in place.

        Raises ValueError on an invalid file — the API layer turns that into
        a 400, same pattern as SkillsService.ingest.
        """
        ext = _CONTENT_TYPE_EXT.get(content_type or "")
        if ext is None:
            raise ValueError("unsupported image type — use PNG, JPEG, or WebP")
        if len(data) > MAX_LOGO_BYTES:
            raise ValueError("logo image exceeds the 2MB size limit")

        blob_path = f"rooms/{room.id}/logo.{ext}"
        await self._blob.upload(blob_path, data)
        room.logo_blob_path = blob_path
        room.logo_source = "custom"
        session.add(
            AuditLog(
                room_id=room.id,
                actor=actor,
                action="room_logo_uploaded",
                detail={"content_type": content_type},
            )
        )
        await session.commit()
