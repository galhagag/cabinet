"""@-mention routing: payload goes exclusively to the tagged agent."""
from .conftest import make_room


def test_mention_routes_only_to_fce(client):
    room = make_room(client, "MentionBank")
    resp = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "@FCE what rolling-window boundary do you recommend?"},
    )
    body = resp.json()
    agent_msgs = [m for m in body["messages"] if m["sender_type"] == "agent"]
    assert len(agent_msgs) == 1, "mention must produce exactly one targeted reply"
    assert agent_msgs[0]["agent_key"] == "fce"

    human = [m for m in body["messages"] if m["sender_type"] == "human"][0]
    assert human["mention_target"] == "fce"
    # No autonomous loop ran; room stays active.
    assert body["room_status"] == "active"


def test_mention_routes_only_to_data_expert(client):
    room = make_room(client, "MentionBank2")
    resp = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "hey @DataExpert — validate the Parquet layout please"},
    )
    agent_msgs = [
        m for m in resp.json()["messages"] if m["sender_type"] == "agent"
    ]
    assert len(agent_msgs) == 1
    assert agent_msgs[0]["agent_key"] == "data_expert"


def test_mention_reply_uses_surrounding_history(client):
    room = make_room(client, "HistoryBank")
    client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "@FCE note: customer processes SEPA credit transfers"},
    )
    resp = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "@FCE given that, which rules first?"},
    )
    reply = [m for m in resp.json()["messages"] if m["sender_type"] == "agent"][0]
    # Mock LLM echoes the tail of the compiled history window.
    assert "which rules first" in reply["content"]
