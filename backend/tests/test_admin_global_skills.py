"""Admin global skills: delete (room_id=NULL skills shared across every room)."""
from .conftest import make_room

MD_SKILL = b"# Global Screening Policy\nAlways screen counterparties above EUR 10k.\n"


def _upload_global_skill(client, agent_key: str = "fce") -> dict:
    resp = client.post(
        f"/api/admin/agents/{agent_key}/skills",
        files={"file": ("global.md", MD_SKILL, "text/markdown")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_oversized_global_skill_upload_rejected(client):
    oversized = b"# Global Too Large\n" + (b"x" * 1_048_577)
    resp = client.post(
        "/api/admin/agents/fce/skills",
        files={"file": ("global.md", oversized, "text/markdown")},
    )
    assert resp.status_code == 413, resp.text


def test_delete_global_skill_removes_it_from_list(client):
    skill = _upload_global_skill(client)
    resp = client.delete(f"/api/admin/agents/fce/skills/{skill['id']}")
    assert resp.status_code == 204

    listed = client.get("/api/admin/agents/fce/skills").json()
    assert listed == []


def test_delete_unknown_skill_404s(client):
    resp = client.delete("/api/admin/agents/fce/skills/not-a-real-id")
    assert resp.status_code == 404


def test_delete_room_scoped_skill_via_admin_route_404s(client):
    room = make_room(client, "RoomScopedBank")
    resp = client.post(
        f"/api/rooms/{room['id']}/agents/fce/skills",
        files={"file": ("room.md", b"# Room Only\nLocal rule.", "text/markdown")},
    )
    assert resp.status_code == 201, resp.text
    room_skill = resp.json()

    resp = client.delete(f"/api/admin/agents/fce/skills/{room_skill['id']}")
    assert resp.status_code == 404


def test_delete_with_wrong_agent_key_404s(client):
    skill = _upload_global_skill(client, agent_key="fce")
    resp = client.delete(f"/api/admin/agents/data_expert/skills/{skill['id']}")
    assert resp.status_code == 404


def test_deleting_global_skill_removes_room_override(client):
    room = make_room(client, "OverrideCascadeBank")
    skill = _upload_global_skill(client)

    resp = client.put(
        f"/api/rooms/{room['id']}/agents/fce/skills/{skill['id']}",
        json={"enabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    resp = client.delete(f"/api/admin/agents/fce/skills/{skill['id']}")
    assert resp.status_code == 204

    listed = client.get(f"/api/rooms/{room['id']}/agents/fce/skills").json()
    assert listed == []
