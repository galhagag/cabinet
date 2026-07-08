"""Real-time stream: room events fan out to connected WebSocket clients."""
from .conftest import make_room


def _drain_until(ws, event_type: str, limit: int = 40) -> dict:
    for _ in range(limit):
        event = ws.receive_json()
        if event.get("type") == event_type:
            return event
    raise AssertionError(f"never received {event_type}")


def test_ws_receives_message_and_pause_events(client):
    room = make_room(client, "WsBank")
    with client.websocket_connect(f"/ws/rooms/{room['id']}") as ws:
        client.post(f"/api/rooms/{room['id']}/messages", json={"content": "go"})

        first = _drain_until(ws, "message_created")
        assert first["message"]["sender_type"] == "human"

        paused = _drain_until(ws, "room_paused")
        assert paused["cycles_used"] == 6
        assert paused["cycle_limit"] == 6


def test_ws_receives_agent_thinking_indicator(client):
    room = make_room(client, "WsBank2")
    with client.websocket_connect(f"/ws/rooms/{room['id']}") as ws:
        client.post(
            f"/api/rooms/{room['id']}/messages",
            json={"content": "@FCE quick check"},
        )
        thinking = _drain_until(ws, "agent_thinking")
        assert thinking["agent_key"] == "fce"
