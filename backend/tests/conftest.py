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


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "CABINET_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    )
    monkeypatch.setenv("CABINET_LLM_MODE", "mock")
    monkeypatch.setenv("CABINET_SECRETS_PROVIDER", "env")
    monkeypatch.setenv("CABINET_BLOB_PROVIDER", "local")
    monkeypatch.setenv("CABINET_LOCAL_BLOB_ROOT", str(tmp_path / "blob"))
    monkeypatch.setenv("CABINET_REALTIME_PROVIDER", "inprocess")

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
