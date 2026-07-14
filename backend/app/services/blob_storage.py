"""BlobStorageProvider: Azure Blob Storage in production, local FS in dev/CI."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..config import Settings
from .secrets import SecretProvider


class BlobStorageProvider(Protocol):
    async def upload(self, path: str, data: bytes) -> str: ...

    async def download(self, path: str) -> bytes: ...

    async def delete(self, path: str) -> None: ...


class LocalBlobStorageProvider:
    """Dev/test provider storing blobs under a local root directory."""

    def __init__(self, root: str) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str) -> Path:
        target = (self._root / path).resolve()
        if not target.is_relative_to(self._root):
            raise ValueError(f"blob path escapes storage root: {path}")
        return target

    async def upload(self, path: str, data: bytes) -> str:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return path

    async def download(self, path: str) -> bytes:
        return self._resolve(path).read_bytes()

    async def delete(self, path: str) -> None:
        self._resolve(path).unlink(missing_ok=True)


class AzureBlobStorageProvider:
    """Production provider backed by Azure Blob Storage.

    The azure SDK is imported lazily; the connection string is resolved
    through the SecretProvider (Key Vault in prod) on first use.
    """

    def __init__(self, settings: Settings, secret_provider: SecretProvider) -> None:
        self._settings = settings
        self._secrets = secret_provider
        self._service = None

    async def _get_service(self):
        if self._service is None:
            from azure.storage.blob.aio import BlobServiceClient

            connection_string = await self._secrets.get_secret(
                "azure-blob-connection-string"
            )
            self._service = BlobServiceClient.from_connection_string(connection_string)
        return self._service

    async def upload(self, path: str, data: bytes) -> str:
        service = await self._get_service()
        blob = service.get_blob_client(
            container=self._settings.blob_container, blob=path
        )
        await blob.upload_blob(data, overwrite=True)
        return path

    async def download(self, path: str) -> bytes:
        service = await self._get_service()
        blob = service.get_blob_client(
            container=self._settings.blob_container, blob=path
        )
        downloader = await blob.download_blob()
        return await downloader.readall()

    async def delete(self, path: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError

        service = await self._get_service()
        blob = service.get_blob_client(
            container=self._settings.blob_container, blob=path
        )
        try:
            await blob.delete_blob()
        except ResourceNotFoundError:
            pass


def build_blob_provider(
    settings: Settings, secret_provider: SecretProvider
) -> BlobStorageProvider:
    if settings.blob_provider == "azure_blob":
        return AzureBlobStorageProvider(settings, secret_provider)
    if settings.blob_provider == "local":
        return LocalBlobStorageProvider(settings.local_blob_root)
    raise ValueError(f"unknown blob provider: {settings.blob_provider}")
