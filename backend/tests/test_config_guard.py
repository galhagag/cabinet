"""Fail-closed production config guard (Design 01 / H1, H2, M8)."""
import pytest

from app.config import ConfigError, Settings


def _prod_settings(**overrides) -> Settings:
    base = dict(
        env="production",
        auth_mode="entra",
        entra_tenant_id="tenant-1",
        entra_client_id="client-1",
        admin_emails="admin@thetaray.com",
        secrets_provider="azure_keyvault",
        allowed_origins="https://app.example.com",
        llm_mode="foundry",
    )
    base.update(overrides)
    return Settings(**base)


def test_prod_settings_fully_configured_validates_clean():
    _prod_settings().validate_for_environment()  # must not raise


def test_dev_settings_default_validates_clean():
    Settings().validate_for_environment()  # dev defaults are all dev-safe


def test_invalid_env_value_raises():
    with pytest.raises(ConfigError, match="CABINET_ENV"):
        Settings(env="production-ish").validate_for_environment()


@pytest.mark.parametrize(
    "overrides,expected_fragment",
    [
        ({"auth_mode": "dev"}, "CABINET_AUTH_MODE"),
        ({"entra_tenant_id": ""}, "CABINET_ENTRA_TENANT_ID"),
        ({"entra_client_id": ""}, "CABINET_ENTRA_CLIENT_ID"),
        ({"admin_emails": ""}, "CABINET_ADMIN_EMAILS"),
        ({"secrets_provider": "env"}, "CABINET_SECRETS_PROVIDER"),
        # Default llm_mode ("mock") must not sail through into staging/prod —
        # that's the exact "agent replies are mocked" failure mode this guard
        # closes off.
        ({"llm_mode": "mock"}, "CABINET_LLM_MODE"),
        ({"llm_mode": ""}, "CABINET_LLM_MODE"),
        ({"llm_mode": "not-a-real-mode"}, "CABINET_LLM_MODE"),
        ({"allowed_origins": "*"}, "CABINET_ALLOWED_ORIGINS"),
        # A wildcard hiding among other origins must be rejected too — not
        # just an exact "*" value. Without this, CORSMiddleware still sees
        # "*" in allow_origins (allow-all) even though the raw string isn't
        # literally "*".
        ({"allowed_origins": "https://app.example.com,*"}, "CABINET_ALLOWED_ORIGINS"),
        ({"allowed_origins": "*, https://app.example.com"}, "CABINET_ALLOWED_ORIGINS"),
        # Whitespace/commas-only must count as "unset", same as "".
        ({"admin_emails": " , "}, "CABINET_ADMIN_EMAILS"),
    ],
)
def test_prod_missing_required_var_raises(overrides, expected_fragment):
    with pytest.raises(ConfigError, match=expected_fragment):
        _prod_settings(**overrides).validate_for_environment()


def test_prod_allows_azure_openai_llm_mode():
    _prod_settings(llm_mode="azure_openai").validate_for_environment()  # must not raise


def test_prod_allows_env_secrets_with_explicit_escape_hatch(monkeypatch):
    monkeypatch.setenv("CABINET_ALLOW_ENV_SECRETS", "1")
    try:
        _prod_settings(secrets_provider="env").validate_for_environment()  # must not raise
    finally:
        monkeypatch.delenv("CABINET_ALLOW_ENV_SECRETS")


def test_bad_int_env_var_raises_config_error(monkeypatch):
    from app.config import reset_settings_cache

    monkeypatch.setenv("CABINET_CYCLE_LIMIT", "not-a-number")
    reset_settings_cache()
    try:
        with pytest.raises(ConfigError, match="CABINET_CYCLE_LIMIT"):
            Settings()
    finally:
        monkeypatch.delenv("CABINET_CYCLE_LIMIT")
        reset_settings_cache()


def test_negative_int_env_var_raises_config_error(monkeypatch):
    from app.config import reset_settings_cache

    monkeypatch.setenv("CABINET_CYCLE_LIMIT", "-1")
    reset_settings_cache()
    try:
        with pytest.raises(ConfigError, match="CABINET_CYCLE_LIMIT"):
            Settings()
    finally:
        monkeypatch.delenv("CABINET_CYCLE_LIMIT")
        reset_settings_cache()


def test_settings_is_frozen():
    settings = Settings()
    with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
        settings.admin_emails = "hacked@evil.example"


@pytest.mark.parametrize(
    "raw",
    ["https://app.example.com,*", "*, https://app.example.com", "*"],
)
def test_cors_origins_never_allows_credentials_alongside_wildcard(raw):
    """`allow_credentials` in main.py is keyed off `"*" not in cors_origins`.

    Any `*` in the parsed list — alone or alongside real origins — must
    disable credentials, since Starlette's CORSMiddleware treats a single
    "*" entry as allow-all-origins and, combined with allow_credentials,
    will reflect back an arbitrary request Origin with
    Access-Control-Allow-Credentials: true (i.e. any origin gets credentialed
    access, not just a browser-rejected header combo).
    """
    settings = Settings(auth_mode="entra", allowed_origins=raw)
    origins = settings.cors_origins
    allow_credentials = settings.auth_mode == "entra" and "*" not in origins
    assert "*" in origins
    assert allow_credentials is False


def test_cors_origins_splits_and_strips():
    settings = Settings(allowed_origins=" https://a.example.com , https://b.example.com ")
    assert settings.cors_origins == ["https://a.example.com", "https://b.example.com"]
