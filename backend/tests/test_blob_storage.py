"""LocalBlobStorageProvider.delete: idempotent removal of a stored blob."""
from app.services.blob_storage import LocalBlobStorageProvider


def test_delete_removes_the_blob(tmp_path):
    provider = LocalBlobStorageProvider(str(tmp_path))
    import asyncio

    asyncio.run(provider.upload("skills/global/fce/x.md", b"content"))
    target = tmp_path / "skills/global/fce/x.md"
    assert target.exists()

    asyncio.run(provider.delete("skills/global/fce/x.md"))
    assert not target.exists()


def test_delete_missing_blob_does_not_raise(tmp_path):
    provider = LocalBlobStorageProvider(str(tmp_path))
    import asyncio

    asyncio.run(provider.delete("skills/global/fce/never-uploaded.md"))
