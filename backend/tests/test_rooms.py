"""Room lifecycle: creation auto-spawns both experts; invites; multi-user join."""
from .conftest import make_room


def test_room_creation_spawns_both_agents(client):
    room = make_room(client, "First National Bank", enrichment="EU payments focus")
    assert room["status"] == "active"
    assert room["cycle_limit"] == 6
    keys = {a["agent_key"] for a in room["agents"]}
    assert keys == {"data_expert", "fce"}

    fetched = client.get(f"/api/rooms/{room['id']}").json()
    assert fetched["customer_name"] == "First National Bank"
    assert fetched["enrichment_prompt"] == "EU payments focus"


def test_duplicate_customer_name_conflict(client):
    make_room(client, "DupBank")
    resp = client.post("/api/rooms", json={"customer_name": "DupBank"})
    assert resp.status_code == 409


def test_invite_and_join_flow(client):
    room = make_room(client, "InviteBank")
    invite = client.post(f"/api/rooms/{room['id']}/invites")
    assert invite.status_code == 201
    token = invite.json()["token"]
    assert token in invite.json()["join_url"]

    joined = client.post(
        "/api/rooms/join",
        json={"token": token, "display_name": "Compliance Kate"},
        headers={"X-User-Email": "kate@bank.example"},
    )
    assert joined.status_code == 200
    assert joined.json()["id"] == room["id"]

    members = client.get(f"/api/rooms/{room['id']}/members").json()
    emails = {m["user_email"] for m in members}
    assert {"dev@thetaray.com", "kate@bank.example"} <= emails

    # Joining twice is idempotent.
    again = client.post(
        "/api/rooms/join",
        json={"token": token},
        headers={"X-User-Email": "kate@bank.example"},
    )
    assert again.status_code == 200
    assert len(client.get(f"/api/rooms/{room['id']}/members").json()) == len(members)


def test_join_with_bad_token_404(client):
    resp = client.post("/api/rooms/join", json={"token": "not-a-real-token"})
    assert resp.status_code == 404
