"""Per-room tool enable/disable toggle — built-in tools stay scoped per room."""
from .conftest import drain_until, make_room


def test_tools_list_defaults_enabled(client):
    room = make_room(client, "ToolsBank1")
    listed = client.get(f"/api/rooms/{room['id']}/agents/fce/tools").json()
    names = {t["name"] for t in listed}
    # No Google Drive is connected for a fresh room, so drive_search is
    # omitted entirely (nothing to search) — only web_search is offered.
    assert names == {"web_search"}
    assert all(t["enabled"] for t in listed)


def test_toggle_off_then_list_reflects_it(client):
    room = make_room(client, "ToolsBank2")
    resp = client.put(
        f"/api/rooms/{room['id']}/agents/fce/tools/web_search",
        json={"enabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    listed = client.get(f"/api/rooms/{room['id']}/agents/fce/tools").json()
    toggled = next(t for t in listed if t["name"] == "web_search")
    assert toggled["enabled"] is False


def test_toggle_back_on_restores_it(client):
    room = make_room(client, "ToolsBank3")
    client.put(f"/api/rooms/{room['id']}/agents/fce/tools/web_search", json={"enabled": False})
    resp = client.put(f"/api/rooms/{room['id']}/agents/fce/tools/web_search", json={"enabled": True})
    assert resp.json()["enabled"] is True


def test_disabling_in_one_room_does_not_affect_another(client):
    room_a = make_room(client, "ToolsBankA")
    room_b = make_room(client, "ToolsBankB")
    client.put(f"/api/rooms/{room_a['id']}/agents/fce/tools/web_search", json={"enabled": False})

    a_listed = client.get(f"/api/rooms/{room_a['id']}/agents/fce/tools").json()
    b_listed = client.get(f"/api/rooms/{room_b['id']}/agents/fce/tools").json()
    assert next(t for t in a_listed if t["name"] == "web_search")["enabled"] is False
    assert next(t for t in b_listed if t["name"] == "web_search")["enabled"] is True


def test_toggle_unknown_tool_404(client):
    room = make_room(client, "ToolsBank4")
    resp = client.put(
        f"/api/rooms/{room['id']}/agents/fce/tools/not-a-real-tool",
        json={"enabled": False},
    )
    assert resp.status_code == 404


def test_toggle_is_idempotent(client):
    room = make_room(client, "ToolsBank5")
    for _ in range(2):
        resp = client.put(
            f"/api/rooms/{room['id']}/agents/fce/tools/web_search",
            json={"enabled": False},
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False


def test_unknown_agent_key_400(client):
    room = make_room(client, "ToolsBank6")
    resp = client.get(f"/api/rooms/{room['id']}/agents/not-a-real-agent/tools")
    assert resp.status_code == 400


def test_ws_receives_agent_tool_toggled(client):
    room = make_room(client, "ToolsBankWs")
    with client.websocket_connect(f"/ws/rooms/{room['id']}") as ws:
        client.put(f"/api/rooms/{room['id']}/agents/fce/tools/web_search", json={"enabled": False})
        event = drain_until(ws, "agent_tool_toggled")
        assert event["tool_name"] == "web_search"
        assert event["enabled"] is False
