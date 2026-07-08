"""SecretProvider: Azure Key Vault in production, env-backed mock in dev/CI.

Secret *names* (e.g. ``google-oauth-client-id``) are shared across providers;
only the resolution backend changes. ``EnvSecretProvider`` maps a name to the
env var ``CABINET_SECRET_<NAME>`` (dashes → underscores, upper-cased) and
falls back to safe deterministic dev defaults so the whole stack boots with
zero configuration.
"""
from __future__ import annotations

import os
import secrets as _secrets
from typing import Protocol

from cryptography.fernet import Fernet

from ..config import Settings


class SecretProvider(Protocol):
    async def get_secret(self, name: str) -> str: ...


# Dev defaults are generated once per process and cached so that everything
# encrypted/signed during a run stays verifiable for the whole run.
_dev_default_cache: dict[str, str] = {}


def _dev_default(name: str) -> str:
    if name in _dev_default_cache:
        return _dev_default_cache[name]
    if name == "token-encryption-key":
        value = Fernet.generate_key().decode()
    elif name == "state-signing-key":
        value = _secrets.token_urlsafe(32)
    elif name == "google-oauth-client-id":
        value = "mock-google-client-id"
    elif name == "google-oauth-client-secret":
        value = "mock-google-client-secret"
    elif name == "foundry-api-key":
        value = "mock-foundry-key"
    else:
        raise KeyError(f"secret not configured: {name}")
    _dev_default_cache[name] = value
    return value


class EnvSecretProvider:
    """Dev/test provider: env vars with deterministic per-process defaults."""

    async def get_secret(self, name: str) -> str:
        env_name = "CABINET_SECRET_" + name.upper().replace("-", "_")
        value = os.environ.get(env_name)
        if value:
            return value
        return _dev_default(name)


class AzureKeyVaultSecretProvider:
    """Production provider backed by Azure Key Vault.

    The azure SDKs are imported lazily so dev/CI environments never need
    them installed.
    """

    def __init__(self, vault_url: str) -> None:
        self._vault_url = vault_url
        self._client = None

    async def get_secret(self, name: str) -> str:
        if self._client is None:
            from azure.identity.aio import DefaultAzureCredential
            from azure.keyvault.secrets.aio import SecretClient

            self._client = SecretClient(
                vault_url=self._vault_url, credential=DefaultAzureCredential()
            )
        secret = await self._client.get_secret(name)
        return secret.value


def build_secret_provider(settings: Settings) -> SecretProvider:
    if settings.secrets_provider == "azure_keyvault":
        return AzureKeyVaultSecretProvider(settings.keyvault_url)
    if settings.secrets_provider == "env":
        return EnvSecretProvider()
    raise ValueError(f"unknown secrets provider: {settings.secrets_provider}")
