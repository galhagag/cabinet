"""Real-time stream: room events fan out to connected WebSocket clients."""
from .conftest import drain_until, make_room


def test_ws_receives_message_and_pause_events(client):
    room = make_room(client, "WsBank")
    with client.websocket_connect(f"/ws/rooms/{room['id']}") as ws:
        client.post(f"/api/rooms/{room['id']}/messages", json={"content": "go"})

        first = drain_until(ws, "message_created")
        assert first["message"]["sender_type"] == "human"

        paused = drain_until(ws, "room_paused")
        assert paused["cycles_used"] == 6
        assert paused["cycle_limit"] == 6


def test_ws_receives_agent_thinking_indicator(client):
    room = make_room(client, "WsBank2")
    with client.websocket_connect(f"/ws/rooms/{room['id']}") as ws:
        client.post(
            f"/api/rooms/{room['id']}/messages",
            json={"content": "@FCE quick check"},
        )
        thinking = drain_until(ws, "agent_thinking")
        assert thinking["agent_key"] == "fce"


def test_ws_cleans_up_on_ungracious_disconnect(client):
    """Any exit path from the receive loop — not just WebSocketDisconnect —
    must still deregister the connection (Design 04 Lows)."""
    room = make_room(client, "WsCleanupBank")
    with client.websocket_connect(f"/ws/rooms/{room['id']}") as ws:
        manager = client.app.state.manager
        assert room["id"] in manager._rooms
    # Context manager exit closes the socket; the server's finally must run.
    manager = client.app.state.manager
    assert room["id"] not in manager._rooms
