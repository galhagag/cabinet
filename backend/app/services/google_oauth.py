"""Google Drive OAuth2 — real authorization-code lifecycle.

Consent URL construction, HMAC-signed ``state`` (itsdangerous), code → token
exchange, refresh-token rotation, and Fernet encryption of tokens at rest.
The httpx transport is injectable so tests exercise the full code path over
``httpx.MockTransport`` — only the HTTPS hop to Google is replaced.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
from cryptography.fernet import Fernet, MultiFernet
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from urllib.parse import urlencode

from ..config import Settings
from ..db.models import GDriveConnection
from .secrets import SecretProvider

STATE_SALT = "gdrive-oauth"
# Refresh proactively when the access token expires within this window.
EXPIRY_SLACK_SECONDS = 60


class GoogleOAuthService:
    def __init__(
        self,
        settings: Settings,
        secret_provider: SecretProvider,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._secrets = secret_provider
        self._transport = transport
        self._fernet: MultiFernet | None = None
        self._serializer: URLSafeTimedSerializer | None = None

    # ------------------------------------------------------------------
    # State signing
    # ------------------------------------------------------------------
    async def _ensure_serializer(self) -> URLSafeTimedSerializer:
        if self._serializer is None:
            key = await self._secrets.get_secret(
                self._settings.state_signing_key_secret
            )
            self._serializer = URLSafeTimedSerializer(key, salt=STATE_SALT)
        return self._serializer

    async def verify_state(self, state: str, max_age: int = 900) -> dict:
        """Verify + decode a signed OAuth state; ValueError on tamper/expiry.

        Async because the signing key comes from the secret provider — on a
        fresh replica (Key Vault) this is a network fetch, so the callback
        must be able to verify state without having served /authorize first.
        """
        serializer = await self._ensure_serializer()
        try:
            return serializer.loads(state, max_age=max_age)
        except BadSignature as exc:  # SignatureExpired subclasses BadSignature
            raise ValueError(f"invalid oauth state: {exc}") from exc

    # ------------------------------------------------------------------
    # Consent URL
    # ------------------------------------------------------------------
    async def authorize_url(self, room_id: str, user_email: str) -> tuple[str, str]:
        client_id = await self._secrets.get_secret(
            self._settings.google_client_id_secret
        )
        serializer = await self._ensure_serializer()
        state = serializer.dumps({"room_id": room_id, "user_email": user_email})
        params = {
            "client_id": client_id,
            "redirect_uri": self._settings.google_redirect_uri,
            "response_type": "code",
            "scope": self._settings.google_scopes,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return f"{self._settings.google_auth_endpoint}?{urlencode(params)}", state

    # ------------------------------------------------------------------
    # Token endpoint
    # ------------------------------------------------------------------
    async def _token_request(self, form: dict[str, str]) -> dict:
        async with httpx.AsyncClient(transport=self._transport) as client:
            response = await client.post(
                self._settings.google_token_endpoint, data=form
            )
        response.raise_for_status()
        return response.json()

    async def exchange_code(self, code: str) -> dict:
        client_id = await self._secrets.get_secret(
            self._settings.google_client_id_secret
        )
        client_secret = await self._secrets.get_secret(
            self._settings.google_client_secret_secret
        )
        return await self._token_request(
            {
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": self._settings.google_redirect_uri,
                "grant_type": "authorization_code",
            }
        )

    async def refresh_access_token(self, refresh_token: str) -> dict:
        client_id = await self._secrets.get_secret(
            self._settings.google_client_id_secret
        )
        client_secret = await self._secrets.get_secret(
            self._settings.google_client_secret_secret
        )
        return await self._token_request(
            {
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
            }
        )

    # ------------------------------------------------------------------
    # Token encryption at rest (Fernet; key from Key Vault in prod)
    # ------------------------------------------------------------------
    def _get_fernet(self) -> MultiFernet:
        if self._fernet is None:
            raise RuntimeError(
                "Fernet key not primed — every code path that encrypts or "
                "decrypts must first `await self._ensure_fernet()`"
            )
        return self._fernet

    async def _ensure_fernet(self) -> MultiFernet:
        if self._fernet is None:
            primary = await self._secrets.get_secret(
                self._settings.token_encryption_key_secret
            )
            keys = [Fernet(primary.encode())]
            try:
                previous = await self._secrets.get_secret(
                    self._settings.token_encryption_key_previous_secret
                )
            except Exception:
                previous = ""
            if previous:
                keys.append(Fernet(previous.encode()))
            self._fernet = MultiFernet(keys)
        return self._fernet

    def encrypt(self, value: str) -> str:
        return self._get_fernet().encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        return self._get_fernet().decrypt(value.encode()).decode()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    async def store_tokens(
        self, session: AsyncSession, room_id: str, token_response: dict
    ) -> GDriveConnection:
        """Upsert the room's connection with freshly-encrypted tokens."""
        await self._ensure_fernet()
        result = await session.execute(
            select(GDriveConnection).where(GDriveConnection.room_id == room_id)
        )
        conn = result.scalar_one_or_none()
        if conn is None:
            conn = GDriveConnection(room_id=room_id)
            session.add(conn)

        conn.access_token_enc = self.encrypt(token_response["access_token"])
        if token_response.get("refresh_token"):
            conn.refresh_token_enc = self.encrypt(token_response["refresh_token"])
        conn.token_expiry = datetime.now(timezone.utc) + timedelta(
            seconds=int(token_response.get("expires_in", 3600))
        )
        conn.scopes = token_response.get("scope", "")
        conn.status = "connected"
        await session.commit()
        return conn

    async def ensure_fresh_access_token(
        self, session: AsyncSession, conn: GDriveConnection
    ) -> str:
        """Return a valid access token, auto-refreshing when (nearly) expired."""
        await self._ensure_fernet()
        expiry = conn.token_expiry
        if expiry is not None and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)  # SQLite drops tzinfo
        now = datetime.now(timezone.utc)

        if expiry is not None and expiry > now + timedelta(
            seconds=EXPIRY_SLACK_SECONDS
        ):
            return self.decrypt(conn.access_token_enc)

        refresh_token = self.decrypt(conn.refresh_token_enc)
        token_response = await self.refresh_access_token(refresh_token)
        conn.access_token_enc = self.encrypt(token_response["access_token"])
        conn.token_expiry = now + timedelta(
            seconds=int(token_response.get("expires_in", 3600))
        )
        await session.commit()
        return token_response["access_token"]
