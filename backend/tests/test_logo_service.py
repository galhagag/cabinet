"""LogoService unit tests: Brandfetch auto-lookup and manual upload, tested
directly against the service — no HTTP layer involved (see test_room_logo_api.py
for the end-to-end path)."""
import httpx

from app.db.base import get_sessionmaker
from app.services.logo import MAX_LOGO_BYTES, LogoService

from .conftest import make_room

_SEARCH_PATH = "/v2/search/"


def _service(client, handler) -> LogoService:
    return LogoService(
        client.app.state.settings,
        client.app.state.secret_provider,
        client.app.state.blob_provider,
        transport=httpx.MockTransport(handler),
    )


async def _fetch(client, service, room_id):
    async with get_sessionmaker()() as session:
        return await service.fetch_for_room(session, room_id)


def test_fetch_success_stores_icon_and_marks_auto(client, monkeypatch):
    monkeypatch.setenv("CABINET_SECRET_BRANDFETCH_CLIENT_ID", "test-client-id")
    room = make_room(client, "Acme Bank")

    def handler(request: httpx.Request) -> httpx.Response:
        if _SEARCH_PATH in str(request.url):
            return httpx.Response(
                200,
                json=[
                    {
                        "domain": "acmebank.com",
                        "icon": "https://cdn.brandfetch.io/acmebank.com/icon.png",
                        "name": "Acme Bank",
                        "claimed": True,
                        "brandId": "abc123",
                    }
                ],
            )
        return httpx.Response(
            200, content=b"fake-png-bytes", headers={"content-type": "image/png"}
        )

    service = _service(client, handler)
    updated = client.portal.call(_fetch, client, service, room["id"])

    assert updated is not None
    assert updated.logo_source == "auto"
    assert updated.logo_blob_path == f"rooms/{room['id']}/logo.png"

    stored = client.portal.call(client.app.state.blob_provider.download, updated.logo_blob_path)
    assert stored == b"fake-png-bytes"


def test_fetch_no_match_marks_none(client, monkeypatch):
    monkeypatch.setenv("CABINET_SECRET_BRANDFETCH_CLIENT_ID", "test-client-id")
    room = make_room(client, "Nobody Bank")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    service = _service(client, handler)
    updated = client.portal.call(_fetch, client, service, room["id"])

    assert updated.logo_source == "none"
    assert updated.logo_blob_path is None


def test_fetch_unsupported_format_marks_none(client, monkeypatch):
    monkeypatch.setenv("CABINET_SECRET_BRANDFETCH_CLIENT_ID", "test-client-id")
    room = make_room(client, "SvgOnly Bank")

    def handler(request: httpx.Request) -> httpx.Response:
        if _SEARCH_PATH in str(request.url):
            return httpx.Response(
                200,
                json=[
                    {
                        "domain": "svgonly.com",
                        "icon": "https://cdn.brandfetch.io/svgonly.com/icon.svg",
                        "name": "SvgOnly",
                        "claimed": False,
                        "brandId": "x",
                    }
                ],
            )
        return httpx.Response(200, content=b"<svg/>", headers={"content-type": "image/svg+xml"})

    service = _service(client, handler)
    updated = client.portal.call(_fetch, client, service, room["id"])

    assert updated.logo_source == "none"
    assert updated.logo_blob_path is None


def test_fetch_network_failure_marks_none_without_raising(client, monkeypatch):
    monkeypatch.setenv("CABINET_SECRET_BRANDFETCH_CLIENT_ID", "test-client-id")
    room = make_room(client, "Flaky Bank")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    service = _service(client, handler)
    updated = client.portal.call(_fetch, client, service, room["id"])

    assert updated.logo_source == "none"


def test_fetch_without_configured_secret_marks_none(client):
    """Default dev/test env: no brandfetch-client-id configured at all — must
    degrade to "none" and must NOT attempt a network call."""
    room = make_room(client, "Unconfigured Bank")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not make a network call without a client id")

    service = _service(client, handler)
    updated = client.portal.call(_fetch, client, service, room["id"])

    assert updated.logo_source == "none"


def test_save_upload_rejects_oversized_file(client):
    room = make_room(client, "BigLogo Bank")
    service = LogoService(
        client.app.state.settings, client.app.state.secret_provider, client.app.state.blob_provider
    )

    async def run():
        from app.db.models import Room

        async with get_sessionmaker()() as session:
            room_row = await session.get(Room, room["id"])
            try:
                await service.save_upload(
                    session,
                    room_row,
                    content_type="image/png",
                    data=b"x" * (MAX_LOGO_BYTES + 1),
                    actor="dev@thetaray.com",
                )
                raise AssertionError("expected ValueError for oversized upload")
            except ValueError as exc:
                assert "2MB" in str(exc)

    client.portal.call(run)


def test_save_upload_rejects_unsupported_type(client):
    room = make_room(client, "WrongType Bank")
    service = LogoService(
        client.app.state.settings, client.app.state.secret_provider, client.app.state.blob_provider
    )

    async def run():
        from app.db.models import Room

        async with get_sessionmaker()() as session:
            room_row = await session.get(Room, room["id"])
            try:
                await service.save_upload(
                    session,
                    room_row,
                    content_type="image/gif",
                    data=b"GIF89a",
                    actor="dev@thetaray.com",
                )
                raise AssertionError("expected ValueError for unsupported type")
            except ValueError:
                pass

    client.portal.call(run)


def test_save_upload_persists_blob_and_marks_custom(client):
    room = make_room(client, "GoodUploadBank")
    service = LogoService(
        client.app.state.settings, client.app.state.secret_provider, client.app.state.blob_provider
    )

    async def run():
        from app.db.models import Room

        async with get_sessionmaker()() as session:
            room_row = await session.get(Room, room["id"])
            await service.save_upload(
                session,
                room_row,
                content_type="image/webp",
                data=b"webp-bytes",
                actor="dev@thetaray.com",
            )
            return room_row.logo_blob_path, room_row.logo_source

    blob_path, source = client.portal.call(run)
    assert blob_path == f"rooms/{room['id']}/logo.webp"
    assert source == "custom"
    stored = client.portal.call(client.app.state.blob_provider.download, blob_path)
    assert stored == b"webp-bytes"
