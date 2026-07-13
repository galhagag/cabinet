"""Per-room skill enable/disable toggle — global skills stay scoped per room."""
from .conftest import drain_until, make_room

MD_SKILL = b"# Cross-Border Rule\nFlag any transfer above EUR 50k.\n"


def _upload_skill(client, room_id: str, agent_key: str = "fce") -> dict:
    resp = client.post(
        f"/api/rooms/{room_id}/agents/{agent_key}/skills",
        files={"file": ("rule.md", MD_SKILL, "text/markdown")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_new_skill_defaults_enabled(client):
    room = make_room(client, "ToggleBank1")
    _upload_skill(client, room["id"])
    listed = client.get(f"/api/rooms/{room['id']}/agents/fce/skills").json()
    assert listed[0]["enabled"] is True


def test_toggle_off_excludes_from_compiled_prompt(client):
    room = make_room(client, "ToggleBank2")
    skill = _upload_skill(client, room["id"])

    resp = client.put(
        f"/api/rooms/{room['id']}/agents/fce/skills/{skill['id']}",
        json={"enabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    listed = client.get(f"/api/rooms/{room['id']}/agents/fce/skills").json()
    assert listed[0]["enabled"] is False

    compiled = client.get(
        f"/api/rooms/{room['id']}/agents/fce/compiled-prompt"
    ).json()["compiled_prompt"]
    assert "Cross-Border Rule" not in compiled


def test_toggle_back_on_restores_it(client):
    room = make_room(client, "ToggleBank3")
    skill = _upload_skill(client, room["id"])
    client.put(
        f"/api/rooms/{room['id']}/agents/fce/skills/{skill['id']}",
        json={"enabled": False},
    )
    resp = client.put(
        f"/api/rooms/{room['id']}/agents/fce/skills/{skill['id']}",
        json={"enabled": True},
    )
    assert resp.json()["enabled"] is True
    compiled = client.get(
        f"/api/rooms/{room['id']}/agents/fce/compiled-prompt"
    ).json()["compiled_prompt"]
    assert "Cross-Border Rule" in compiled


def test_disabling_global_skill_in_one_room_does_not_affect_another(client):
    room_a = make_room(client, "ToggleBankA")
    room_b = make_room(client, "ToggleBankB")
    resp = client.post(
        "/api/admin/agents/fce/skills",
        files={"file": ("global-rule.md", MD_SKILL, "text/markdown")},
    )
    assert resp.status_code == 201, resp.text
    global_skill = resp.json()

    client.put(
        f"/api/rooms/{room_a['id']}/agents/fce/skills/{global_skill['id']}",
        json={"enabled": False},
    )

    a_listed = client.get(f"/api/rooms/{room_a['id']}/agents/fce/skills").json()
    b_listed = client.get(f"/api/rooms/{room_b['id']}/agents/fce/skills").json()
    assert a_listed[0]["enabled"] is False
    assert b_listed[0]["enabled"] is True


def test_toggle_unknown_skill_404(client):
    room = make_room(client, "ToggleBank4")
    resp = client.put(
        f"/api/rooms/{room['id']}/agents/fce/skills/not-a-real-id",
        json={"enabled": False},
    )
    assert resp.status_code == 404


def test_toggle_is_idempotent(client):
    room = make_room(client, "ToggleBank5")
    skill = _upload_skill(client, room["id"])
    for _ in range(2):
        resp = client.put(
            f"/api/rooms/{room['id']}/agents/fce/skills/{skill['id']}",
            json={"enabled": False},
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False


def test_ws_receives_agent_skill_toggled(client):
    room = make_room(client, "ToggleBankWs")
    skill = _upload_skill(client, room["id"])
    with client.websocket_connect(f"/ws/rooms/{room['id']}") as ws:
        client.put(
            f"/api/rooms/{room['id']}/agents/fce/skills/{skill['id']}",
            json={"enabled": False},
        )
        event = drain_until(ws, "agent_skill_toggled")
        assert event["skill_id"] == skill["id"]
        assert event["enabled"] is False
