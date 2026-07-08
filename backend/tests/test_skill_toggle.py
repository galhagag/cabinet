"""Skill enable/disable toggles: compiled-prompt effect + authorization."""
from .conftest import make_room

MD = b"# Wire Rules\nFlag same-day round-trip wires above EUR 50k."


def _upload(client, room_id: str, agent_key: str = "fce") -> dict:
    resp = client.post(
        f"/api/rooms/{room_id}/agents/{agent_key}/skills",
        files={"file": ("wire.md", MD, "text/markdown")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _compiled(client, room_id: str, agent_key: str = "fce") -> str:
    return client.get(
        f"/api/rooms/{room_id}/agents/{agent_key}/compiled-prompt"
    ).json()["compiled_prompt"]


def test_skill_starts_enabled_and_toggles_out_of_prompt(client):
    room = make_room(client, "ToggleBank")
    skill = _upload(client, room["id"])
    assert skill["enabled"] is True
    assert "round-trip wires" in _compiled(client, room["id"])

    off = client.patch(
        f"/api/rooms/{room['id']}/agents/fce/skills/{skill['id']}",
        json={"enabled": False},
    )
    assert off.status_code == 200
    assert off.json()["enabled"] is False
    assert "round-trip wires" not in _compiled(client, room["id"])

    # Disabled skills stay listed (registry + blob intact), just marked off.
    listed = client.get(f"/api/rooms/{room['id']}/agents/fce/skills").json()
    assert [s["enabled"] for s in listed] == [False]

    on = client.patch(
        f"/api/rooms/{room['id']}/agents/fce/skills/{skill['id']}",
        json={"enabled": True},
    )
    assert on.json()["enabled"] is True
    assert "round-trip wires" in _compiled(client, room["id"])


def test_toggle_requires_membership(client):
    room = make_room(client, "ToggleAuthBank")
    skill = _upload(client, room["id"])
    resp = client.patch(
        f"/api/rooms/{room['id']}/agents/fce/skills/{skill['id']}",
        json={"enabled": False},
        headers={"X-User-Email": "stranger@elsewhere.example"},
    )
    assert resp.status_code == 403


def test_room_route_cannot_toggle_global_skill(client):
    uploaded = client.post(
        "/api/admin/agents/fce/skills",
        files={"file": ("global.md", b"# Global Policy\nAlways screen.", "text/markdown")},
    ).json()
    room = make_room(client, "GlobalToggleBank")

    denied = client.patch(
        f"/api/rooms/{room['id']}/agents/fce/skills/{uploaded['id']}",
        json={"enabled": False},
    )
    assert denied.status_code == 403

    # Admin route can — and the change reaches every room's compiled prompt.
    off = client.patch(
        f"/api/admin/skills/{uploaded['id']}", json={"enabled": False}
    )
    assert off.status_code == 200
    assert "Always screen" not in _compiled(client, room["id"])


def test_toggle_unknown_or_mismatched_skill_404(client):
    room = make_room(client, "Toggle404Bank")
    skill = _upload(client, room["id"], agent_key="fce")
    # Wrong agent in the path.
    resp = client.patch(
        f"/api/rooms/{room['id']}/agents/data_expert/skills/{skill['id']}",
        json={"enabled": False},
    )
    assert resp.status_code == 404
    # Skill belonging to a different room.
    other = make_room(client, "Toggle404OtherBank")
    resp = client.patch(
        f"/api/rooms/{other['id']}/agents/fce/skills/{skill['id']}",
        json={"enabled": False},
    )
    assert resp.status_code == 404
