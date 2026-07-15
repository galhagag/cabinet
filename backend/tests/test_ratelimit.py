"""Phase 1 abuse controls: app-level route rate limiting."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from .conftest import _configure_env, make_room

MD_SKILL = b"# Rate Limit Skill\nUse reviewer approvals."


@pytest.fixture()
def limited_client(tmp_path, monkeypatch):
    _configure_env(tmp_path, monkeypatch, "ratelimit.db")
    monkeypatch.setenv("CABINET_RATELIMIT_PROVIDER", "inprocess")
    monkeypatch.setenv("CABINET_RATELIMIT_ROOM_CREATE_LIMIT", "2")
    monkeypatch.setenv("CABINET_RATELIMIT_ROOM_CREATE_WINDOW", "60")
    monkeypatch.setenv("CABINET_RATELIMIT_MESSAGE_LIMIT", "2")
    monkeypatch.setenv("CABINET_RATELIMIT_MESSAGE_WINDOW", "60")
    monkeypatch.setenv("CABINET_RATELIMIT_RESUME_LIMIT", "1")
    monkeypatch.setenv("CABINET_RATELIMIT_RESUME_WINDOW", "60")
    monkeypatch.setenv("CABINET_RATELIMIT_INVITE_LIMIT", "2")
    monkeypatch.setenv("CABINET_RATELIMIT_INVITE_WINDOW", "60")
    monkeypatch.setenv("CABINET_RATELIMIT_SKILL_UPLOAD_LIMIT", "1")
    monkeypatch.setenv("CABINET_RATELIMIT_SKILL_UPLOAD_WINDOW", "60")

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


def test_room_create_rate_limit_returns_429(limited_client):
    assert limited_client.post("/api/rooms", json={"customer_name": "LimitBank1"}).status_code == 201
    assert limited_client.post("/api/rooms", json={"customer_name": "LimitBank2"}).status_code == 201

    resp = limited_client.post("/api/rooms", json={"customer_name": "LimitBank3"})
    assert resp.status_code == 429, resp.text
    assert int(resp.headers["Retry-After"]) >= 1


def test_message_rate_limit_is_room_scoped(limited_client):
    room_a = make_room(limited_client, "RoomScopedA")
    room_b = make_room(limited_client, "RoomScopedB")

    assert limited_client.post(
        f"/api/rooms/{room_a['id']}/messages", json={"content": "first"}
    ).status_code == 200
    assert limited_client.post(
        f"/api/rooms/{room_a['id']}/messages", json={"content": "second"}
    ).status_code == 200

    blocked = limited_client.post(
        f"/api/rooms/{room_a['id']}/messages", json={"content": "third"}
    )
    assert blocked.status_code == 429, blocked.text

    allowed = limited_client.post(
        f"/api/rooms/{room_b['id']}/messages", json={"content": "other room"}
    )
    assert allowed.status_code == 200, allowed.text


def test_message_rate_limit_is_user_scoped(limited_client):
    room = make_room(limited_client, "UserScopedBank")
    invite = limited_client.post(f"/api/rooms/{room['id']}/invites")
    assert invite.status_code == 201, invite.text

    joined = limited_client.post(
        "/api/rooms/join",
        json={"token": invite.json()["token"], "display_name": "Second User"},
        headers={"X-User-Email": "second@bank.example"},
    )
    assert joined.status_code == 200, joined.text

    assert limited_client.post(
        f"/api/rooms/{room['id']}/messages", json={"content": "owner one"}
    ).status_code == 200
    assert limited_client.post(
        f"/api/rooms/{room['id']}/messages", json={"content": "owner two"}
    ).status_code == 200
    assert limited_client.post(
        f"/api/rooms/{room['id']}/messages", json={"content": "owner three"}
    ).status_code == 429

    other = limited_client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "member one"},
        headers={"X-User-Email": "second@bank.example"},
    )
    assert other.status_code == 200, other.text


def test_invite_and_skill_upload_limits_return_429(limited_client):
    room = make_room(limited_client, "InviteSkillBank")

    assert limited_client.post(f"/api/rooms/{room['id']}/invites").status_code == 201
    assert limited_client.post(f"/api/rooms/{room['id']}/invites").status_code == 201
    invite_blocked = limited_client.post(f"/api/rooms/{room['id']}/invites")
    assert invite_blocked.status_code == 429, invite_blocked.text

    assert limited_client.post(
        f"/api/rooms/{room['id']}/agents/fce/skills",
        files={"file": ("skill.md", MD_SKILL, "text/markdown")},
    ).status_code == 201
    upload_blocked = limited_client.post(
        f"/api/rooms/{room['id']}/agents/fce/skills",
        files={"file": ("skill.md", MD_SKILL, "text/markdown")},
    )
    assert upload_blocked.status_code == 429, upload_blocked.text

    assert limited_client.post(
        "/api/admin/agents/fce/skills",
        files={"file": ("global-skill.md", MD_SKILL, "text/markdown")},
    ).status_code == 201
    global_upload_blocked = limited_client.post(
        "/api/admin/agents/fce/skills",
        files={"file": ("global-skill.md", MD_SKILL, "text/markdown")},
    )
    assert global_upload_blocked.status_code == 429, global_upload_blocked.text


def test_resume_rate_limit_returns_429(limited_client):
    room = make_room(limited_client, "ResumeLimitBank")
    kickoff = limited_client.post(
        f"/api/rooms/{room['id']}/messages", json={"content": "go"}
    )
    assert kickoff.status_code == 200, kickoff.text

    first = limited_client.post(f"/api/rooms/{room['id']}/resume")
    assert first.status_code == 200, first.text

    second = limited_client.post(f"/api/rooms/{room['id']}/resume")
    assert second.status_code == 429, second.text
    assert int(second.headers["Retry-After"]) >= 1