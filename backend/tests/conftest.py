"""Integration-test harness.

Runs the real FastAPI app (full lifespan: DB init, config seeding, provider
wiring) over Starlette's TestClient against a per-test SQLite database with
CABINET_LLM_MODE=mock — the identical code paths production exercises, minus
network and credentials.
"""
from __future__ import annotations

import asyncio
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient


def _configure_env(tmp_path, monkeypatch, db_name: str) -> None:
    monkeypatch.setenv(
        "CABINET_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / db_name}"
    )
    monkeypatch.setenv("CABINET_LLM_MODE", "mock")
    monkeypatch.setenv("CABINET_SECRETS_PROVIDER", "env")
    monkeypatch.setenv("CABINET_BLOB_PROVIDER", "local")
    monkeypatch.setenv("CABINET_LOCAL_BLOB_ROOT", str(tmp_path / "blob"))
    monkeypatch.setenv("CABINET_REALTIME_PROVIDER", "inprocess")
    monkeypatch.setenv("CABINET_SKIP_LOCAL_DOTENV", "1")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    _configure_env(tmp_path, monkeypatch, "test.db")

    from app.config import reset_settings_cache
    from app.db.base import dispose_engine

    reset_settings_cache()
    asyncio.run(dispose_engine())

    from app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client

    reset_settings_cache()
    asyncio.run(dispose_engine())


@pytest.fixture()
def entra_client(tmp_path, monkeypatch):
    """Same harness as ``client`` but with CABINET_AUTH_MODE=entra.

    The Entra validator is constructed for real by the app's lifespan (a
    tenant id + audience are configured); tests then swap its JWKS transport
    for a MockTransport via ``install_mock_entra`` before issuing requests.
    """
    _configure_env(tmp_path, monkeypatch, "entra_test.db")
    monkeypatch.setenv("CABINET_AUTH_MODE", "entra")
    monkeypatch.setenv("CABINET_ENTRA_TENANT_ID", "test-tenant")
    monkeypatch.setenv("CABINET_ENTRA_CLIENT_ID", "test-api-client-id")

    from app.config import reset_settings_cache
    from app.db.base import dispose_engine

    reset_settings_cache()
    asyncio.run(dispose_engine())

    from app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client

    reset_settings_cache()
    asyncio.run(dispose_engine())


def make_room(client: TestClient, name: str = "Acme Bank", enrichment: str | None = None) -> dict:
    resp = client.post(
        "/api/rooms", json={"customer_name": name, "enrichment_prompt": enrichment}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def google_token_handler(calls: list):
    """httpx.MockTransport handler emulating Google's OAuth token endpoint."""

    def handler(request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        calls.append(form)
        if form.get("grant_type") == "authorization_code":
            assert form.get("code"), "missing authorization code"
            return httpx.Response(
                200,
                json={
                    "access_token": "ya29.mock-access-token",
                    "refresh_token": "1//mock-refresh-token",
                    "expires_in": 3600,
                    "scope": "https://www.googleapis.com/auth/drive.readonly",
                    "token_type": "Bearer",
                },
            )
        if form.get("grant_type") == "refresh_token":
            assert form.get("refresh_token") == "1//mock-refresh-token"
            return httpx.Response(
                200,
                json={
                    "access_token": "ya29.refreshed-access-token",
                    "expires_in": 3600,
                    "scope": "https://www.googleapis.com/auth/drive.readonly",
                    "token_type": "Bearer",
                },
            )
        return httpx.Response(400, json={"error": "unsupported_grant_type"})

    return handler


def make_entra_keypair():
    """Generate an RSA keypair + matching JWKS doc for Entra auth tests."""
    from jose import jwk
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = jwk.RSAKey(
        algorithm="RS256", key=private_key.public_key()
    ).to_dict()
    public_jwk["kid"] = "test-kid-1"
    public_jwk["use"] = "sig"
    return private_key, {"keys": [public_jwk]}


def make_entra_token(private_key, *, kid="test-kid-1", **claim_overrides):
    from jose import jwt as jose_jwt
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    claims = {
        "iss": "https://login.microsoftonline.com/test-tenant/v2.0",
        "aud": "test-api-client-id",
        "exp": now + timedelta(hours=1),
        "iat": now,
        "preferred_username": "alice@thetaray.com",
    }
    claims.update(claim_overrides)
    from cryptography.hazmat.primitives import serialization

    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jose_jwt.encode(claims, pem, algorithm="RS256", headers={"kid": kid})


def install_mock_entra(app, jwks: dict):
    """Swap the app's EntraTokenValidator for one backed by a MockTransport."""
    import httpx

    from app.services.entra_auth import EntraTokenValidator

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=jwks)

    app.state.entra_validator = EntraTokenValidator(
        app.state.settings, transport=httpx.MockTransport(handler)
    )


def install_mock_google(app) -> list:
    """Swap the app's GoogleOAuthService for one backed by a MockTransport.

    The full production code path (state signing → verification → code
    exchange → Fernet encryption → persistence → refresh) still runs; only
    the HTTPS hop to accounts.google.com is replaced.
    """
    from app.services.google_oauth import GoogleOAuthService

    calls: list = []
    app.state.google_oauth = GoogleOAuthService(
        app.state.settings,
        app.state.secret_provider,
        transport=httpx.MockTransport(google_token_handler(calls)),
    )
    return calls
