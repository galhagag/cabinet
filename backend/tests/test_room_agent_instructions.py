"""Per-room, per-agent instructions: optional, empty by default, room-member editable."""
from .conftest import drain_until, make_room


def test_instructions_empty_by_default(client):
    room = make_room(client, "InstructionsBank")
    resp = client.get(f"/api/rooms/{room['id']}/agents/fce")
    assert resp.status_code == 200
    body = resp.json()
    assert body["instructions"] == ""
    assert body["agent_key"] == "fce"
    assert "system_prompt" in body


def test_update_instructions_then_get_reflects_it(client):
    room = make_room(client, "InstructionsBank2")
    resp = client.put(
        f"/api/rooms/{room['id']}/agents/data_expert/instructions",
        json={"instructions": "Focus on SEPA Instant rails for this customer."},
    )
    assert resp.status_code == 200
    assert resp.json()["instructions"] == "Focus on SEPA Instant rails for this customer."

    fetched = client.get(f"/api/rooms/{room['id']}/agents/data_expert").json()
    assert fetched["instructions"] == "Focus on SEPA Instant rails for this customer."


def test_instructions_are_per_agent_not_shared(client):
    room = make_room(client, "InstructionsBank3")
    client.put(
        f"/api/rooms/{room['id']}/agents/data_expert/instructions",
        json={"instructions": "Data Expert only context."},
    )
    fce = client.get(f"/api/rooms/{room['id']}/agents/fce").json()
    assert fce["instructions"] == ""


def test_instructions_are_per_room_not_shared(client):
    room_a = make_room(client, "InstructionsBankA")
    room_b = make_room(client, "InstructionsBankB")
    client.put(
        f"/api/rooms/{room_a['id']}/agents/fce/instructions",
        json={"instructions": "Room A only."},
    )
    b_instructions = client.get(f"/api/rooms/{room_b['id']}/agents/fce").json()["instructions"]
    assert b_instructions == ""


def test_empty_instructions_payload_is_accepted(client):
    room = make_room(client, "InstructionsBank4")
    resp = client.put(
        f"/api/rooms/{room['id']}/agents/fce/instructions",
        json={"instructions": ""},
    )
    assert resp.status_code == 200
    assert resp.json()["instructions"] == ""


def test_unknown_agent_key_400(client):
    room = make_room(client, "InstructionsBank5")
    resp = client.get(f"/api/rooms/{room['id']}/agents/not-a-real-agent")
    assert resp.status_code == 400


def test_non_member_cannot_read_or_update_instructions(client):
    room = make_room(client, "InstructionsBank6")
    resp = client.get(
        f"/api/rooms/{room['id']}/agents/fce",
        headers={"X-User-Email": "outsider@bank.example"},
    )
    assert resp.status_code == 403

    resp2 = client.put(
        f"/api/rooms/{room['id']}/agents/fce/instructions",
        json={"instructions": "hijacked"},
        headers={"X-User-Email": "outsider@bank.example"},
    )
    assert resp2.status_code == 403


def test_ws_receives_agent_instructions_updated(client):
    room = make_room(client, "InstructionsBankWs")
    with client.websocket_connect(f"/ws/rooms/{room['id']}") as ws:
        client.put(
            f"/api/rooms/{room['id']}/agents/fce/instructions",
            json={"instructions": "Live update test."},
        )
        event = drain_until(ws, "agent_instructions_updated")
        assert event["agent_key"] == "fce"
        assert event["room_id"] == room["id"]


def test_instructions_history_records_old_and_new_text(client):
    room = make_room(client, "InstructionsHistoryBank")
    client.put(
        f"/api/rooms/{room['id']}/agents/fce/instructions",
        json={"instructions": "First version."},
    )
    client.put(
        f"/api/rooms/{room['id']}/agents/fce/instructions",
        json={"instructions": "Second version."},
    )
    resp = client.get(f"/api/rooms/{room['id']}/agents/fce/instructions/history")
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 2
    # newest first
    assert entries[0]["old_instructions"] == "First version."
    assert entries[0]["new_instructions"] == "Second version."
    assert entries[0]["actor"]
    assert entries[1]["old_instructions"] == ""
    assert entries[1]["new_instructions"] == "First version."


def test_instructions_history_is_per_agent(client):
    room = make_room(client, "InstructionsHistoryBank2")
    client.put(
        f"/api/rooms/{room['id']}/agents/data_expert/instructions",
        json={"instructions": "Data Expert only."},
    )
    resp = client.get(f"/api/rooms/{room['id']}/agents/fce/instructions/history")
    assert resp.status_code == 200
    assert resp.json() == []


def test_instructions_history_non_member_403(client):
    room = make_room(client, "InstructionsHistoryBank3")
    resp = client.get(
        f"/api/rooms/{room['id']}/agents/fce/instructions/history",
        headers={"X-User-Email": "outsider@bank.example"},
    )
    assert resp.status_code == 403
