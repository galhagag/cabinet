"""Per-room, per-agent token usage summary."""
from .conftest import make_room


def test_usage_zero_before_any_agent_reply(client):
    room = make_room(client, "UsageBank1")
    resp = client.get(f"/api/rooms/{room['id']}/agents/data_expert/usage")
    assert resp.status_code == 200
    assert resp.json() == {
        "agent_key": "data_expert",
        "message_count": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
    }


def test_usage_accumulates_from_agent_replies(client):
    room = make_room(client, "UsageBank2")
    client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "@data_expert kick off"},
    )

    usage = client.get(f"/api/rooms/{room['id']}/agents/data_expert/usage").json()
    assert usage["message_count"] == 1
    assert usage["total_input_tokens"] > 0
    assert usage["total_output_tokens"] > 0

    fce_usage = client.get(f"/api/rooms/{room['id']}/agents/fce/usage").json()
    assert fce_usage["message_count"] == 0


def test_usage_unknown_agent_400(client):
    room = make_room(client, "UsageBank3")
    resp = client.get(f"/api/rooms/{room['id']}/agents/nope/usage")
    assert resp.status_code == 400
