"""Local dev must load infra/.env — see app/config.py.

`uvicorn app.main:app --reload` (the README quick start) never sees
infra/.env on its own; without an explicit load, CABINET_SECRET_GOOGLE_OAUTH_CLIENT_ID
and friends silently fell back to the "mock-google-client-id" dev default,
which Google's OAuth server rejects with "Error 401: invalid_client".
"""
import os

from app.config import INFRA_ENV_PATH, _load_local_dev_env


def test_infra_env_path_points_at_infra_dotenv():
    assert INFRA_ENV_PATH.parent.name == "infra"
    assert INFRA_ENV_PATH.name == ".env"


def test_fills_in_a_secret_missing_from_the_process_env(tmp_path, monkeypatch):
    monkeypatch.delenv("CABINET_SECRET_GOOGLE_OAUTH_CLIENT_ID", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("CABINET_SECRET_GOOGLE_OAUTH_CLIENT_ID=real-client-id\n")

    _load_local_dev_env(env_file)

    assert os.environ["CABINET_SECRET_GOOGLE_OAUTH_CLIENT_ID"] == "real-client-id"


def test_never_overrides_a_secret_already_set(tmp_path, monkeypatch):
    monkeypatch.setenv("CABINET_SECRET_GOOGLE_OAUTH_CLIENT_ID", "already-set")
    env_file = tmp_path / ".env"
    env_file.write_text("CABINET_SECRET_GOOGLE_OAUTH_CLIENT_ID=from-file\n")

    _load_local_dev_env(env_file)

    assert os.environ["CABINET_SECRET_GOOGLE_OAUTH_CLIENT_ID"] == "already-set"


def test_missing_file_is_a_no_op(tmp_path):
    _load_local_dev_env(tmp_path / "does-not-exist.env")
