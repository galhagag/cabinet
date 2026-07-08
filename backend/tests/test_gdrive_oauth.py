"""Google Drive OAuth2: authorize URL, signed state, code exchange,
encrypted persistence, refresh — full production code path over MockTransport."""
import asyncio

from .conftest import install_mock_google, make_room


def test_authorize_url_and_signed_state(client):
    room = make_room(client, "DriveBank")
    resp = client.get(f"/api/rooms/{room['id']}/gdrive/authorize")
    assert resp.status_code == 200
    body = resp.json()
    assert "accounts.google.com" in body["authorize_url"]
    assert "drive.readonly" in body["authorize_url"]
    assert body["state"] in body["authorize_url"]

    status = client.get(f"/api/rooms/{room['id']}/gdrive/status").json()
    assert status["status"] == "pending"


def test_callback_exchanges_code_and_persists_encrypted_tokens(client):
    calls = install_mock_google(client.app)
    room = make_room(client, "DriveBank2")
    state = client.get(f"/api/rooms/{room['id']}/gdrive/authorize").json()["state"]

    resp = client.get(
        "/api/gdrive/callback", params={"code": "mock-auth-code", "state": state}
    )
    assert resp.status_code == 200, resp.text
    assert calls[0]["grant_type"] == "authorization_code"
    assert calls[0]["code"] == "mock-auth-code"

    status = client.get(f"/api/rooms/{room['id']}/gdrive/status").json()
    assert status["status"] == "connected"

    # Tokens are Fernet-encrypted at rest — never stored in plaintext.
    from app.db.base import get_sessionmaker
    from app.db.models import GDriveConnection
    from sqlalchemy import select

    async def fetch():
        async with get_sessionmaker()() as session:
            result = await session.execute(
                select(GDriveConnection).where(GDriveConnection.room_id == room["id"])
            )
            return result.scalar_one()

    conn = client.portal.call(fetch)
    assert conn.access_token_enc and "ya29" not in conn.access_token_enc
    assert conn.refresh_token_enc and "mock-refresh" not in conn.refresh_token_enc
    assert client.app.state.google_oauth.decrypt(conn.access_token_enc) == (
        "ya29.mock-access-token"
    )


def test_callback_rejects_tampered_state(client):
    install_mock_google(client.app)
    make_room(client, "DriveBank3")
    resp = client.get(
        "/api/gdrive/callback", params={"code": "x", "state": "forged.state.value"}
    )
    assert resp.status_code == 400


def test_folder_link_and_status(client):
    calls = install_mock_google(client.app)
    room = make_room(client, "DriveBank4")
    state = client.get(f"/api/rooms/{room['id']}/gdrive/authorize").json()["state"]
    client.get("/api/gdrive/callback", params={"code": "c", "state": state})

    resp = client.post(
        f"/api/rooms/{room['id']}/gdrive/folder",
        json={"folder_id": "1AbCdEf", "folder_name": "Onboarding Docs"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "linked"
    assert body["google_folder_id"] == "1AbCdEf"
    assert body["google_folder_name"] == "Onboarding Docs"


def test_expired_access_token_is_refreshed(client):
    calls = install_mock_google(client.app)
    room = make_room(client, "DriveBank5")
    state = client.get(f"/api/rooms/{room['id']}/gdrive/authorize").json()["state"]
    client.get("/api/gdrive/callback", params={"code": "c", "state": state})

    from datetime import datetime, timedelta, timezone

    from app.db.base import get_sessionmaker
    from app.db.models import GDriveConnection
    from sqlalchemy import select

    svc = client.app.state.google_oauth

    async def expire_then_refresh():
        async with get_sessionmaker()() as session:
            result = await session.execute(
                select(GDriveConnection).where(GDriveConnection.room_id == room["id"])
            )
            conn = result.scalar_one()
            conn.token_expiry = datetime.now(timezone.utc) - timedelta(minutes=5)
            await session.commit()
            return await svc.ensure_fresh_access_token(session, conn)

    token = client.portal.call(expire_then_refresh)
    assert token == "ya29.refreshed-access-token"
    assert any(c.get("grant_type") == "refresh_token" for c in calls)
