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
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

# `uvicorn app.main:app --reload` (the README quick start) never sees
# infra/.env on its own — nothing else loads it into the process. Load the
# local secret overrides here so CABINET_SECRET_* values reach
# EnvSecretProvider, but do not automatically import the rest of infra/.env:
# a production-like file can otherwise silently redirect local dev to Azure
# services (database, auth, LLM) and make the app appear hung at startup.
# Callers that intentionally want the full file can opt in with
# CABINET_LOAD_FULL_INFRA_ENV=1.
INFRA_ENV_PATH = Path(__file__).resolve().parent.parent.parent / "infra" / ".env"
FULL_LOCAL_DOTENV_FLAG = "CABINET_LOAD_FULL_INFRA_ENV"
LOCAL_SECRET_ENV_PREFIX = "CABINET_SECRET_"


def _load_local_dev_env(path: Path = INFRA_ENV_PATH) -> None:
    if os.environ.get(FULL_LOCAL_DOTENV_FLAG) == "1":
        load_dotenv(path, override=False)
        return

    for name, value in dotenv_values(path).items():
        if value is None or not name.startswith(LOCAL_SECRET_ENV_PREFIX):
            continue
        os.environ.setdefault(name, value)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _split_csv(value: str) -> list[str]:
    """Parse a comma-separated env value, dropping empty/whitespace entries."""
    return [item.strip() for item in value.split(",") if item.strip()]


class ConfigError(Exception):
    """Raised when configuration is invalid or unsafe for the environment."""


def _env_int(name: str, default: int, *, min_value: int | None = None) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if min_value is not None and value < min_value:
        raise ConfigError(f"{name} must be >= {min_value}, got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    # --- Core ---------------------------------------------------------
    app_name: str = "Cabinet of Experts"
    # "dev" | "staging" | "production" — gates validate_for_environment().
    env: str = field(default_factory=lambda: _env("CABINET_ENV", "dev"))
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

    # --- LLM runtime (GPT on Microsoft Foundry, via the Azure OpenAI SDK) ---
    azure_openai_endpoint: str = field(
        default_factory=lambda: _env("CABINET_AZURE_OPENAI_ENDPOINT", "")
    )
    azure_openai_deployment: str = field(
        default_factory=lambda: _env("CABINET_AZURE_OPENAI_DEPLOYMENT", "")
    )
    azure_openai_api_version: str = field(
        default_factory=lambda: _env("CABINET_AZURE_OPENAI_API_VERSION", "2024-10-21")
    )
    # Set to "entra" to authenticate with Microsoft Entra ID instead of an API key.
    azure_openai_auth: str = field(
        default_factory=lambda: _env("CABINET_AZURE_OPENAI_AUTH", "api_key")
    )
    # Secret NAME in the secret provider (Key Vault in prod), not the key itself.
    azure_openai_api_key_secret: str = field(
        default_factory=lambda: _env(
            "CABINET_AZURE_OPENAI_API_KEY_SECRET", "azure-openai-api-key"
        )
    )
    agent_max_tokens: int = field(
        default_factory=lambda: _env_int("CABINET_AGENT_MAX_TOKENS", 2048, min_value=1)
    )
    # How many recent messages are compiled into an agent's context window.
    history_window: int = field(
        default_factory=lambda: _env_int("CABINET_HISTORY_WINDOW", 40, min_value=1)
    )

    # --- Loop control ---------------------------------------------------
    # Hard product default: max autonomous agent-to-agent turns before pause.
    default_cycle_limit: int = field(
        default_factory=lambda: _env_int("CABINET_CYCLE_LIMIT", 6, min_value=1)
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
    token_encryption_key_previous_secret: str = "token-encryption-key-previous"

    # --- Invites -----------------------------------------------------------
    invite_ttl_hours: int = field(
        default_factory=lambda: _env_int("CABINET_INVITE_TTL_HOURS", 168, min_value=1)
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
    # Comma-separated CORS origins. "*" is the dev default; production must
    # set this to the real frontend origin(s) — enforced below.
    allowed_origins: str = field(
        default_factory=lambda: _env("CABINET_ALLOWED_ORIGINS", "*")
    )

    @property
    def cors_origins(self) -> list[str]:
        """Parsed CABINET_ALLOWED_ORIGINS — single source of truth for both
        the boot guard below and the CORS middleware in main.py, so a
        wildcard hiding among other origins (e.g. "https://a.com,*") can't
        pass one parser and slip past the other.
        """
        return _split_csv(self.allowed_origins)

    def validate_for_environment(self) -> None:
        """Refuse to boot with an unsafe config outside dev.

        Raising here (called once from the FastAPI lifespan, before any
        provider is built) turns "deployed, forgot an env var" into a loud
        crash-loop instead of silent identity impersonation or an open admin
        surface — see H1/H2/M8 in the 2026-07-12 review.
        """
        if self.env not in ("dev", "staging", "production"):
            raise ConfigError(
                f"CABINET_ENV must be one of dev|staging|production, got {self.env!r}"
            )
        if self.env == "dev":
            return
        if self.auth_mode != "entra":
            raise ConfigError(
                "CABINET_AUTH_MODE must be 'entra' when CABINET_ENV is staging/production"
            )
        if not self.entra_tenant_id:
            raise ConfigError(
                "CABINET_ENTRA_TENANT_ID must be set when CABINET_ENV is staging/production"
            )
        if not self.entra_client_id:
            raise ConfigError(
                "CABINET_ENTRA_CLIENT_ID must be set when CABINET_ENV is staging/production"
            )
        if not _split_csv(self.admin_emails):
            raise ConfigError(
                "CABINET_ADMIN_EMAILS must be set when CABINET_ENV is staging/production"
            )
        if self.secrets_provider != "azure_keyvault" and _env("CABINET_ALLOW_ENV_SECRETS") != "1":
            raise ConfigError(
                "CABINET_SECRETS_PROVIDER must be 'azure_keyvault' when CABINET_ENV is "
                "staging/production (set CABINET_ALLOW_ENV_SECRETS=1 to override)"
            )
        # Reject "*" anywhere in the list, not just an exact "*" value — a
        # value like "https://app.example.com,*" would otherwise sail past
        # an exact-match check yet still make CORSMiddleware treat every
        # origin as allowed (allow_all_origins = "*" in allow_origins).
        origins = self.cors_origins
        if not origins or "*" in origins:
            raise ConfigError(
                "CABINET_ALLOWED_ORIGINS must be a non-wildcard value when CABINET_ENV "
                "is staging/production"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    if os.environ.get("CABINET_SKIP_LOCAL_DOTENV") != "1":
        _load_local_dev_env()
    return Settings()


def reset_settings_cache() -> None:
    """Test helper: force settings re-read after env changes."""
    get_settings.cache_clear()
