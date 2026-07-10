"""Microsoft Entra ID auth: JWT/JWKS verification, no shared secret.

Exercises the full production code path (signature verify against JWKS →
issuer/audience/expiry checks → email claim → room membership authz) with
only the HTTPS hop to login.microsoftonline.com replaced by MockTransport —
the same approach ``test_gdrive_oauth.py`` uses for Google's token endpoint.
"""
from datetime import datetime, timedelta, timezone

import pytest

from .conftest import install_mock_entra, make_entra_keypair, make_entra_token


def test_dev_mode_still_uses_trusted_header(client):
    """CABINET_AUTH_MODE=dev (default) is unaffected by the Entra code path."""
    resp = client.get("/api/rooms", headers={"X-User-Email": "someone@thetaray.com"})
    assert resp.status_code == 200


def _create_room(client, token: str | None, name: str = "EntraBank"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(
        "/api/rooms",
        json={"customer_name": name, "enrichment_prompt": None},
        headers=headers,
    )


def test_entra_mode_rejects_missing_token(entra_client):
    _, jwks = make_entra_keypair()
    install_mock_entra(entra_client.app, jwks)
    resp = _create_room(entra_client, None)
    assert resp.status_code == 401


def test_entra_mode_accepts_valid_token_and_extracts_email(entra_client):
    private_key, jwks = make_entra_keypair()
    install_mock_entra(entra_client.app, jwks)
    token = make_entra_token(private_key, preferred_username="alice@thetaray.com")

    create = entra_client.post(
        "/api/rooms",
        json={"customer_name": "EntraBank", "enrichment_prompt": None},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create.status_code == 201, create.text
    room_id = create.json()["id"]

    # Room membership was granted to the token's verified email, not a
    # client-supplied header — a different caller is refused.
    other_token = make_entra_token(private_key, preferred_username="mallory@evil.com")
    forbidden = entra_client.get(
        f"/api/rooms/{room_id}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert forbidden.status_code == 403

    allowed = entra_client.get(
        f"/api/rooms/{room_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert allowed.status_code == 200


def test_entra_mode_rejects_expired_token(entra_client):
    private_key, jwks = make_entra_keypair()
    install_mock_entra(entra_client.app, jwks)
    now = datetime.now(timezone.utc)
    expired = make_entra_token(
        private_key, exp=now - timedelta(minutes=5), iat=now - timedelta(hours=1)
    )
    resp = _create_room(entra_client, expired)
    assert resp.status_code == 401


def test_entra_mode_rejects_wrong_audience(entra_client):
    private_key, jwks = make_entra_keypair()
    install_mock_entra(entra_client.app, jwks)
    token = make_entra_token(private_key, aud="some-other-app")
    resp = _create_room(entra_client, token)
    assert resp.status_code == 401


def test_entra_mode_rejects_wrong_issuer(entra_client):
    private_key, jwks = make_entra_keypair()
    install_mock_entra(entra_client.app, jwks)
    token = make_entra_token(
        private_key, iss="https://login.microsoftonline.com/other-tenant/v2.0"
    )
    resp = _create_room(entra_client, token)
    assert resp.status_code == 401


def test_entra_mode_rejects_unsigned_or_tampered_token(entra_client):
    private_key, jwks = make_entra_keypair()
    install_mock_entra(entra_client.app, jwks)
    token = make_entra_token(private_key)
    tampered = token[:-4] + "abcd"
    resp = _create_room(entra_client, tampered)
    assert resp.status_code == 401


def test_entra_mode_rejects_unknown_kid(entra_client):
    private_key, jwks = make_entra_keypair()
    install_mock_entra(entra_client.app, jwks)
    token = make_entra_token(private_key, kid="never-published-kid")
    resp = _create_room(entra_client, token)
    assert resp.status_code == 401


def test_entra_validator_requires_tenant_and_client_id(monkeypatch):
    from app.config import Settings, reset_settings_cache
    from app.services.entra_auth import EntraTokenValidator

    reset_settings_cache()
    settings = Settings(auth_mode="entra", entra_tenant_id="", entra_client_id="")
    with pytest.raises(ValueError):
        EntraTokenValidator(settings)
