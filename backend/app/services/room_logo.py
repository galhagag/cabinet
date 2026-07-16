"""Room logo fetch/upload helpers."""
from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import quote

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..agents.orchestrator import RealtimeBroker
from ..db.models import AuditLog, Room
from .blob_storage import BlobStorageProvider
from .secrets import SecretProvider

LOGO_SOURCE_PENDING = "pending"
LOGO_SOURCE_AUTO = "auto"
LOGO_SOURCE_CUSTOM = "custom"
LOGO_SOURCE_NONE = "none"

MAX_LOGO_UPLOAD_BYTES = 2 * 1024 * 1024
UPLOAD_READ_CHUNK_SIZE = 64 * 1024

_CONTENT_TYPE_TO_EXTENSION = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}
_EXTENSION_TO_CONTENT_TYPE = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def normalize_content_type(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def extension_for_content_type(content_type: str | None) -> str | None:
    return _CONTENT_TYPE_TO_EXTENSION.get(normalize_content_type(content_type))


def content_type_for_blob_path(path: str) -> str:
    return _EXTENSION_TO_CONTENT_TYPE.get(PurePosixPath(path).suffix.lower(), "application/octet-stream")


def extension_from_url(url: str) -> str | None:
    suffix = PurePosixPath(httpx.URL(url).path).suffix.lower()
    if suffix in _EXTENSION_TO_CONTENT_TYPE:
        return ".jpg" if suffix == ".jpeg" else suffix
    return None


def logo_url_for_room(room: Room) -> str | None:
    return f"/api/rooms/{room.id}/logo" if room.logo_blob_path else None


def room_logo_blob_path(room_id: str, extension: str) -> str:
    return f"rooms/{room_id}/logo{extension}"


def extract_icon_url(payload: object) -> str | None:
    if isinstance(payload, list):
        candidates = payload
    elif isinstance(payload, dict) and isinstance(payload.get("results"), list):
        candidates = payload["results"]
    else:
        candidates = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        icon = item.get("icon")
        if isinstance(icon, str) and icon:
            return icon
        if isinstance(icon, dict):
            icon_url = icon.get("url")
            if isinstance(icon_url, str) and icon_url:
                return icon_url
    return None


class RoomLogoService:
    def __init__(
        self,
        blob_provider: BlobStorageProvider,
        secret_provider: SecretProvider,
        sessionmaker: async_sessionmaker[AsyncSession],
        broker: RealtimeBroker,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._blob = blob_provider
        self._secrets = secret_provider
        self._sessionmaker = sessionmaker
        self._broker = broker
        self._transport = transport

    async def upload_logo_bytes(self, room: Room, *, data: bytes, content_type: str) -> str:
        extension = extension_for_content_type(content_type)
        if extension is None:
            raise ValueError(f"unsupported logo content type: {content_type}")
        path = room_logo_blob_path(room.id, extension)
        await self._blob.upload(path, data)
        return path

    async def fetch_for_room(self, room_id: str, customer_name: str) -> None:
        blob_path: str | None = None
        logo_source = LOGO_SOURCE_NONE
        try:
            client_id = await self._secrets.get_secret("brandfetch-client-id")
            search_url = f"https://api.brandfetch.io/v2/search/{quote(customer_name, safe='')}"
            async with httpx.AsyncClient(
                timeout=10.0,
                follow_redirects=True,
                transport=self._transport,
            ) as client:
                search_response = await client.get(search_url, params={"c": client_id})
                search_response.raise_for_status()
                icon_url = extract_icon_url(search_response.json())
                if icon_url:
                    image_response = await client.get(icon_url)
                    image_response.raise_for_status()
                    extension = extension_for_content_type(
                        image_response.headers.get("content-type")
                    ) or extension_from_url(icon_url)
                    if extension is not None and image_response.content:
                        blob_path = room_logo_blob_path(room_id, extension)
                        await self._blob.upload(blob_path, image_response.content)
                        logo_source = LOGO_SOURCE_AUTO
        except Exception:
            blob_path = None
            logo_source = LOGO_SOURCE_NONE

        old_path: str | None = None
        async with self._sessionmaker() as session:
            room = await session.get(Room, room_id)
            if room is None or room.deleted_at is not None:
                if blob_path is not None:
                    await self._blob.delete(blob_path)
                return
            if room.logo_source == LOGO_SOURCE_CUSTOM:
                if blob_path is not None:
                    await self._blob.delete(blob_path)
                return
            old_path = room.logo_blob_path
            room.logo_blob_path = blob_path
            room.logo_source = logo_source
            session.add(
                AuditLog(
                    room_id=room.id,
                    actor="system",
                    action="room_logo_fetched",
                    detail={"logo_source": room.logo_source},
                )
            )
            await session.commit()

        if old_path and old_path != blob_path:
            await self._blob.delete(old_path)
        await self._broker.publish(
            room_id,
            {
                "type": "room_logo_updated",
                "room_id": room_id,
                "logo_url": f"/api/rooms/{room_id}/logo" if blob_path else None,
                "logo_source": logo_source,
            },
        )