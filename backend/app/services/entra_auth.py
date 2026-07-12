"""Microsoft Entra ID (Azure AD v2.0) access-token validation.

This is what backs ``get_current_user_email`` in production
(``CABINET_AUTH_MODE=entra``): a bearer token presented by the frontend
(acquired via MSAL) is verified against the tenant's public JWKS — RS256
signature, issuer, audience, expiry — with no shared secret ever touching
the backend. Dev/test keeps the trusted ``X-User-Email`` header instead.

The JWKS transport is injectable so tests exercise the full validation
path (signature verify → claim extraction) over ``httpx.MockTransport``,
the same pattern used for Google OAuth (``services/google_oauth.py``).
"""
from __future__ import annotations

from typing import Any

import httpx
from jose import jwt
from jose.exceptions import JOSEError

from ..config import Settings


class EntraTokenError(Exception):
    """Raised for any malformed, unverifiable, or expired access token."""


class EntraTokenValidator:
    """Validates Entra ID access tokens for one tenant + one API audience."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not settings.entra_tenant_id or not settings.entra_client_id:
            raise ValueError(
                "CABINET_ENTRA_TENANT_ID and CABINET_ENTRA_CLIENT_ID are "
                "required when CABINET_AUTH_MODE=entra"
            )
        self._audience = settings.entra_client_id
        self._issuer = f"https://login.microsoftonline.com/{settings.entra_tenant_id}/v2.0"
        self._jwks_uri = (
            f"https://login.microsoftonline.com/{settings.entra_tenant_id}"
            "/discovery/v2.0/keys"
        )
        self._transport = transport
        self._jwks: dict[str, Any] = {"keys": []}

    async def _fetch_jwks(self) -> dict[str, Any]:
        async with httpx.AsyncClient(transport=self._transport) as client:
            response = await client.get(self._jwks_uri)
        response.raise_for_status()
        return response.json()

    async def _get_signing_key(self, kid: str) -> dict[str, Any]:
        keys = {k["kid"]: k for k in self._jwks.get("keys", [])}
        if kid not in keys:
            # Refetch on an unrecognized kid — handles Entra's routine key
            # rotation without needing a fixed TTL.
            self._jwks = await self._fetch_jwks()
            keys = {k["kid"]: k for k in self._jwks.get("keys", [])}
        if kid not in keys:
            raise EntraTokenError(f"no signing key found for kid={kid!r}")
        return keys[kid]

    async def validate(self, token: str) -> str:
        """Verify signature/issuer/audience/expiry; return the caller's email.

        Raises ``EntraTokenError`` for any validation failure.
        """
        try:
            header = jwt.get_unverified_header(token)
        except JOSEError as exc:
            raise EntraTokenError(f"malformed token header: {exc}") from exc

        kid = header.get("kid")
        if not kid:
            raise EntraTokenError("token header missing 'kid'")

        key = await self._get_signing_key(kid)

        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
            )
        except JOSEError as exc:
            raise EntraTokenError(f"token validation failed: {exc}") from exc

        email = (
            claims.get("preferred_username")
            or claims.get("email")
            or claims.get("upn")
        )
        if not email:
            raise EntraTokenError(
                "token has no preferred_username/email/upn claim"
            )
        return email.lower()
