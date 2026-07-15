"""Room logo API: serving, manual upload, the auto-fetch-on-creation path
reflected through GET /api/rooms/{id}, and the Entra query-token fallback."""
import httpx

from .conftest import (
    drain_until,
    install_mock_entra,
    install_mock_logo_service,
    make_entra_keypair,
    make_entra_token,
    make_room,
)


def _mock_logo_handler():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/v2/search/" in str(request.url):
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

    return handler


def test_room_creation_triggers_auto_logo_fetch(client, monkeypatch):
    monkeypatch.setenv("CABINET_SECRET_BRANDFETCH_CLIENT_ID", "test-client-id")
    install_mock_logo_service(client.app, httpx.MockTransport(_mock_logo_handler()))

    room = make_room(client, "Acme Bank")
    # The create-room response body is serialized before the scheduled
    # BackgroundTasks callback runs (FastAPI computes it from the endpoint's
    # return value, which is built immediately after `add_task` merely
    # *registers* the job) — so it always reflects the pre-fetch state,
    # regardless of how fast the background fetch actually completes.
    assert room["logo_source"] == "pending"
    assert room["logo_url"] is None

    # By the time `make_room` returns, though, the background task has
    # already run to completion — TestClient's portal.call blocks for the
    # *whole* ASGI call, background tasks included. A follow-up read reflects
    # the now-completed auto-fetch.
    fetched = client.get(f"/api/rooms/{room['id']}").json()
    assert fetched["logo_source"] == "auto"
    assert fetched["logo_url"] == f"/api/rooms/{room['id']}/logo"


def test_room_creation_without_configured_client_id_has_no_logo(client):
    room = make_room(client, "Unconfigured Bank")
    assert room["logo_source"] == "pending"
    assert room["logo_url"] is None

    fetched = client.get(f"/api/rooms/{room['id']}").json()
    assert fetched["logo_source"] == "none"
    assert fetched["logo_url"] is None


def test_get_logo_streams_bytes(client, monkeypatch):
    monkeypatch.setenv("CABINET_SECRET_BRANDFETCH_CLIENT_ID", "test-client-id")
    install_mock_logo_service(client.app, httpx.MockTransport(_mock_logo_handler()))
    room = make_room(client, "Acme Bank")

    resp = client.get(f"/api/rooms/{room['id']}/logo")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == b"fake-png-bytes"


def test_get_logo_404_when_none_set(client):
    room = make_room(client, "NoLogo Bank")
    resp = client.get(f"/api/rooms/{room['id']}/logo")
    assert resp.status_code == 404


def test_upload_overrides_logo_and_broadcasts(client):
    room = make_room(client, "UploadBank")

    with client.websocket_connect(f"/ws/rooms/{room['id']}") as ws:
        resp = client.post(
            f"/api/rooms/{room['id']}/logo",
            files={"file": ("logo.png", b"custom-bytes", "image/png")},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["logo_source"] == "custom"
        assert body["logo_url"] == f"/api/rooms/{room['id']}/logo"

        event = drain_until(ws, "room_logo_updated")
        assert event["logo_source"] == "custom"
        assert event["logo_url"] == body["logo_url"]

    served = client.get(f"/api/rooms/{room['id']}/logo")
    assert served.content == b"custom-bytes"


def test_upload_rejects_oversized_file(client):
    from app.services.logo import MAX_LOGO_BYTES

    room = make_room(client, "TooBigBank")
    resp = client.post(
        f"/api/rooms/{room['id']}/logo",
        files={"file": ("logo.png", b"x" * (MAX_LOGO_BYTES + 1), "image/png")},
    )
    assert resp.status_code == 400


def test_upload_rejects_unsupported_type(client):
    room = make_room(client, "GifBank")
    resp = client.post(
        f"/api/rooms/{room['id']}/logo",
        files={"file": ("logo.gif", b"GIF89a", "image/gif")},
    )
    assert resp.status_code == 400


def test_upload_requires_membership(client):
    room = make_room(client, "MembersOnlyBank")
    resp = client.post(
        f"/api/rooms/{room['id']}/logo",
        files={"file": ("logo.png", b"bytes", "image/png")},
        headers={"X-User-Email": "outsider@example.com"},
    )
    assert resp.status_code == 403


def test_get_logo_via_query_token_in_entra_mode(entra_client):
    private_key, jwks = make_entra_keypair()
    install_mock_entra(entra_client.app, jwks)
    token = make_entra_token(private_key)

    room = entra_client.post(
        "/api/rooms",
        json={"customer_name": "EntraBank", "enrichment_prompt": None},
        headers={"Authorization": f"Bearer {token}"},
    ).json()

    entra_client.post(
        f"/api/rooms/{room['id']}/logo",
        files={"file": ("logo.png", b"entra-bytes", "image/png")},
        headers={"Authorization": f"Bearer {token}"},
    )

    # No Authorization header — only the query param, exactly like an <img src>.
    resp = entra_client.get(f"/api/rooms/{room['id']}/logo?access_token={token}")
    assert resp.status_code == 200
    assert resp.content == b"entra-bytes"

    unauthorized = entra_client.get(f"/api/rooms/{room['id']}/logo")
    assert unauthorized.status_code == 401
