"""Secrets key stability (Design 08 / M2)."""
import asyncio

import pytest

from app.config import Settings
from app.services.secrets import EnvSecretProvider


def test_env_provider_without_settings_still_generates_dev_default():
    async def scenario():
        provider = EnvSecretProvider()
        key = await provider.get_secret("token-encryption-key")
        assert key
    asyncio.run(scenario())


def test_env_provider_refuses_ephemeral_crypto_key_outside_dev(monkeypatch):
    async def scenario():
        monkeypatch.delenv("CABINET_SECRET_TOKEN_ENCRYPTION_KEY", raising=False)
        settings = Settings(env="staging")
        provider = EnvSecretProvider(settings)
        with pytest.raises(RuntimeError, match="CABINET_SECRET_TOKEN_ENCRYPTION_KEY"):
            await provider.get_secret("token-encryption-key")
    asyncio.run(scenario())


def test_env_provider_allows_non_crypto_secret_outside_dev(monkeypatch):
    """Only the two crypto-key names are strict outside dev — other dev
    defaults (e.g. the mock Google client id) are unaffected."""
    # test_env_file_loading.py's test_fills_in_a_secret_missing_from_the_process_env
    # loads this same var into the real os.environ via load_dotenv (not via
    # monkeypatch), so it can leak across test files depending on run order;
    # clear it explicitly so this test is hermetic regardless of order.
    monkeypatch.delenv("CABINET_SECRET_GOOGLE_OAUTH_CLIENT_ID", raising=False)

    async def scenario():
        settings = Settings(env="staging")
        provider = EnvSecretProvider(settings)
        value = await provider.get_secret("google-oauth-client-id")
        assert value == "mock-google-client-id"
    asyncio.run(scenario())
