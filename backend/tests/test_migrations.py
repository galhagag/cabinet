"""Alembic migration roundtrip (Design 05 / H13)."""
import os
import subprocess
import sys


def test_alembic_upgrade_and_downgrade_roundtrip(tmp_path):
    db_path = tmp_path / "alembic_test.db"
    env = {**os.environ, "CABINET_DATABASE_URL": f"sqlite+aiosqlite:///{db_path}"}
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_dir, env=env, capture_output=True, text=True,
    )
    assert upgrade.returncode == 0, upgrade.stderr

    downgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        cwd=backend_dir, env=env, capture_output=True, text=True,
    )
    assert downgrade.returncode == 0, downgrade.stderr
