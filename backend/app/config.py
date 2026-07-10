"""Application configuration.

Every external dependency (LLM, secrets, blob storage, realtime fan-out,
database) sits behind a provider switch so that development and CI run fully
mocked while production flips environment variables only — no code changes.

Production values (Azure):
    CABINET_LLM_MODE=foundry
    CABINET_SECRETS_PROVIDER=azure_keyvault   + CABINET_KEYVAULT_URL
    CABINET_BLOB_PROVIDER=azure_blob
    CABINET_REALTIME_PROVIDER=azure_webpubsub
    CABINET_DATABASE_URL=postgresql+asyncpg://...   (or via Key Vault)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    # --- Core ---------------------------------------------------------
    app_name: str = "Cabinet of Experts"
    database_url: str = field(
        default_factory=lambda: _env(
            "CABINET_DATABASE_URL", "sqlite+aiosqlite:///./cabinet.db"
        )
    )

    # --- LLM runtime (Microsoft Foundry / Claude) ----------------------
    # "mock"    → deterministic scripted agents (dev/CI, no network)
    # "foundry" → AnthropicFoundry client against Azure AI (MaaS)
    llm_mode: str = field(default_factory=lambda: _env("CABINET_LLM_MODE", "mock"))
    foundry_resource: str = field(
        default_factory=lambda: _env("CABINET_FOUNDRY_RESOURCE", "")
    )
    # Secret NAME in the secret provider (Key Vault in prod), not the key itself.
    foundry_api_key_secret: str = field(
        default_factory=lambda: _env(
            "CABINET_FOUNDRY_API_KEY_SECRET", "foundry-api-key"
        )
    )
    # Set to "entra" to authenticate with Microsoft Entra ID instead of an API key.
    foundry_auth: str = field(default_factory=lambda: _env("CABINET_FOUNDRY_AUTH", "api_key"))
    foundry_model: str = field(
        default_factory=lambda: _env("CABINET_FOUNDRY_MODEL", "claude-opus-4-8")
    )
    agent_max_tokens: int = field(
        default_factory=lambda: int(_env("CABINET_AGENT_MAX_TOKENS", "2048"))
    )
    # How many recent messages are compiled into an agent's context window.
    history_window: int = field(
        default_factory=lambda: int(_env("CABINET_HISTORY_WINDOW", "40"))
    )

    # --- Loop control ---------------------------------------------------
    # Hard product default: max autonomous agent-to-agent turns before pause.
    default_cycle_limit: int = field(
        default_factory=lambda: int(_env("CABINET_CYCLE_LIMIT", "6"))
    )

    # --- Providers ------------------------------------------------------
    secrets_provider: str = field(
        default_factory=lambda: _env("CABINET_SECRETS_PROVIDER", "env")
    )
    keyvault_url: str = field(default_factory=lambda: _env("CABINET_KEYVAULT_URL", ""))
    blob_provider: str = field(
        default_factory=lambda: _env("CABINET_BLOB_PROVIDER", "local")
    )
    local_blob_root: str = field(
        default_factory=lambda: _env("CABINET_LOCAL_BLOB_ROOT", "./.blob")
    )
    blob_container: str = field(
        default_factory=lambda: _env("CABINET_BLOB_CONTAINER", "cabinet-skills")
    )
    realtime_provider: str = field(
        default_factory=lambda: _env("CABINET_REALTIME_PROVIDER", "inprocess")
    )
    webpubsub_hub: str = field(default_factory=lambda: _env("CABINET_WEBPUBSUB_HUB", "cabinet"))

    # --- Google OAuth2 ----------------------------------------------------
    # Secret NAMES resolved through the SecretProvider (Key Vault in prod).
    google_client_id_secret: str = "google-oauth-client-id"
    google_client_secret_secret: str = "google-oauth-client-secret"
    google_redirect_uri: str = field(
        default_factory=lambda: _env(
            "CABINET_GOOGLE_REDIRECT_URI",
            "http://localhost:8000/api/gdrive/callback",
        )
    )
    google_scopes: str = "https://www.googleapis.com/auth/drive.readonly"
    google_auth_endpoint: str = field(
        default_factory=lambda: _env(
            "CABINET_GOOGLE_AUTH_ENDPOINT",
            "https://accounts.google.com/o/oauth2/v2/auth",
        )
    )
    google_token_endpoint: str = field(
        default_factory=lambda: _env(
            "CABINET_GOOGLE_TOKEN_ENDPOINT",
            "https://oauth2.googleapis.com/token",
        )
    )

    # --- Crypto / signing -------------------------------------------------
    # Names of secrets used for token encryption and OAuth state signing.
    token_encryption_key_secret: str = "token-encryption-key"
    state_signing_key_secret: str = "state-signing-key"

    # --- Invites -----------------------------------------------------------
    invite_ttl_hours: int = field(
        default_factory=lambda: int(_env("CABINET_INVITE_TTL_HOURS", "168"))
    )

    # --- Authorization -------------------------------------------------------
    # Comma-separated allowlist for /api/admin/*. Empty ⇒ open (dev only);
    # production MUST set this (or replace with an Entra ID role check).
    admin_emails: str = field(default_factory=lambda: _env("CABINET_ADMIN_EMAILS", ""))

    # --- Authentication --------------------------------------------------------
    # "dev"   → trusted X-User-Email header (no verification; dev/test only)
    # "entra" → Microsoft Entra ID access tokens verified against the tenant's
    #           JWKS (signature, issuer, audience, expiry) — no shared secret.
    auth_mode: str = field(default_factory=lambda: _env("CABINET_AUTH_MODE", "dev"))
    # Directory (tenant) ID of the Entra ID tenant issuing tokens.
    entra_tenant_id: str = field(
        default_factory=lambda: _env("CABINET_ENTRA_TENANT_ID", "")
    )
    # Application (client) ID of the *API* app registration — the expected
    # token audience. Note: this is distinct from the frontend SPA client ID
    # used by MSAL to acquire tokens.
    entra_client_id: str = field(
        default_factory=lambda: _env("CABINET_ENTRA_CLIENT_ID", "")
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Test helper: force settings re-read after env changes."""
    get_settings.cache_clear()
