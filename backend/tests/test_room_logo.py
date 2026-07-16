"""Room logo fetch/upload API coverage."""
from __future__ import annotations

import httpx

from app.services.room_logo import RoomLogoService

from .conftest import drain_until, make_room


def install_mock_room_logo_service(app, handler) -> None:
    current = app.state.room_logo_service
    app.state.room_logo_service = RoomLogoService(
        app.state.blob_provider,
        app.state.secret_provider,
        current._sessionmaker,
        app.state.broker,
        transport=httpx.MockTransport(handler),
    )


def test_room_creation_returns_pending_logo_and_background_falls_back_to_none(client):
    room = make_room(client, "PendingLogoBank")
    assert room["logo_source"] == "pending"
    assert room["logo_url"] is None

    fetched = client.get(f"/api/rooms/{room['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["logo_source"] == "none"
    assert fetched.json()["logo_url"] is None


def test_room_logo_background_fetch_sets_auto_logo_and_broadcasts(client, monkeypatch):
    room = make_room(client, "AutoLogoBank")
    monkeypatch.setenv("CABINET_SECRET_BRANDFETCH_CLIENT_ID", "test-brandfetch-client-id")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.brandfetch.io":
            return httpx.Response(200, json=[{"icon": "https://cdn.example.com/logo.png"}])
        if request.url.host == "cdn.example.com":
            return httpx.Response(200, content=b"png-bytes", headers={"Content-Type": "image/png"})
        raise AssertionError(f"unexpected request: {request.url}")

    install_mock_room_logo_service(client.app, handler)
    with client.websocket_connect(f"/ws/rooms/{room['id']}") as ws:
        client.portal.call(
            client.app.state.room_logo_service.fetch_for_room,
            room["id"],
            room["customer_name"],
        )
        event = drain_until(ws, "room_logo_updated")
        assert event["logo_source"] == "auto"
        assert event["logo_url"] == f"/api/rooms/{room['id']}/logo"

    fetched = client.get(f"/api/rooms/{room['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["logo_source"] == "auto"
    assert fetched.json()["logo_url"] == f"/api/rooms/{room['id']}/logo"


def test_upload_room_logo_persists_blob_and_broadcasts(client):
    room = make_room(client, "UploadLogoBank")

    with client.websocket_connect(f"/ws/rooms/{room['id']}") as ws:
        resp = client.post(
            f"/api/rooms/{room['id']}/logo",
            files={"file": ("logo.png", b"png-data", "image/png")},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["logo_source"] == "custom"
        assert resp.json()["logo_url"] == f"/api/rooms/{room['id']}/logo"

        event = drain_until(ws, "room_logo_updated")
        assert event["logo_source"] == "custom"

    image = client.get(f"/api/rooms/{room['id']}/logo")
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/png")
    assert image.content == b"png-data"


def test_upload_room_logo_rejects_wrong_type(client):
    room = make_room(client, "BadLogoBank")
    resp = client.post(
        f"/api/rooms/{room['id']}/logo",
        files={"file": ("logo.gif", b"gif-data", "image/gif")},
    )
    assert resp.status_code == 400


def test_upload_room_logo_requires_membership(client):
    room = make_room(client, "PrivateLogoBank")
    resp = client.post(
        f"/api/rooms/{room['id']}/logo",
        files={"file": ("logo.png", b"png-data", "image/png")},
        headers={"X-User-Email": "outsider@bank.example"},
    )
    assert resp.status_code == 403