"""Editing the latest human message keeps history visible but retires it.

The room transcript remains append-only for audit purposes: the original
human message and the replies it triggered stay queryable, but future room
previews and agent context ignore superseded rows.
"""
from app.db.base import get_sessionmaker
from app.db.models import Room

from .conftest import make_room


def _agent_msgs(messages):
    return [m for m in messages if m["sender_type"] == "agent"]


def _human_msgs(messages):
    return [m for m in messages if m["sender_type"] == "human"]


def test_edit_latest_human_message_supersedes_current_turn(client):
    room = make_room(client, "EditLatestBank")

    posted = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "Give a one-line status and wrap up."},
    ).json()
    original = _human_msgs(posted["messages"])[0]
    original_reply = _agent_msgs(posted["messages"])[0]

    resp = client.post(
        f"/api/rooms/{room['id']}/messages/{original['id']}/edit",
        json={"content": "Actually, focus on the data layer and wrap up."},
    )
    assert resp.status_code == 200
    body = resp.json()

    assert set(body["superseded_message_ids"]) == {original["id"], original_reply["id"]}
    assert body["cycles_used"] == 1

    replacement_human = _human_msgs(body["messages"])[0]
    replacement_reply = _agent_msgs(body["messages"])[0]
    assert replacement_human["edit_of_id"] == original["id"]
    assert replacement_human["superseded_at"] is None
    assert "wrap up" in replacement_human["content"].lower()
    assert replacement_reply["superseded_at"] is None

    history = {
        msg["id"]: msg for msg in client.get(f"/api/rooms/{room['id']}/messages").json()
    }
    assert history[original["id"]]["superseded_at"] is not None
    assert history[original_reply["id"]]["superseded_at"] is not None
    assert history[replacement_human["id"]]["edit_of_id"] == original["id"]


def test_cannot_edit_another_members_message(client):
    room = make_room(client, "EditOwnershipBank")
    posted = client.post(
        f"/api/rooms/{room['id']}/messages", json={"content": "hello, wrap up."}
    ).json()
    original = _human_msgs(posted["messages"])[0]

    token = client.post(f"/api/rooms/{room['id']}/invites").json()["token"]
    client.post(
        "/api/rooms/join",
        json={"token": token},
        headers={"X-User-Email": "kate@bank.example"},
    )

    resp = client.post(
        f"/api/rooms/{room['id']}/messages/{original['id']}/edit",
        json={"content": "trying to hijack this message"},
        headers={"X-User-Email": "kate@bank.example"},
    )
    assert resp.status_code == 403


def test_cannot_edit_agent_message(client):
    room = make_room(client, "EditAgentBank")
    posted = client.post(
        f"/api/rooms/{room['id']}/messages", json={"content": "hello, wrap up."}
    ).json()
    agent_msg = _agent_msgs(posted["messages"])[0]

    resp = client.post(
        f"/api/rooms/{room['id']}/messages/{agent_msg['id']}/edit",
        json={"content": "nope"},
    )
    assert resp.status_code == 403


def test_cannot_edit_when_target_is_no_longer_latest_human_turn(client):
    room = make_room(client, "EditStaleBank")
    first = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "first question, wrap up."},
    ).json()
    original = _human_msgs(first["messages"])[0]

    second = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "second question, wrap up."},
    )
    assert second.status_code == 200

    resp = client.post(
        f"/api/rooms/{room['id']}/messages/{original['id']}/edit",
        json={"content": "edited too late"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "message is no longer the latest editable turn"


def test_room_preview_uses_latest_non_superseded_message(client):
    room = make_room(client, "EditPreviewBank")

    posted = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "first wording, wrap up."},
    ).json()
    original = _human_msgs(posted["messages"])[0]
    first_reply = _agent_msgs(posted["messages"])[0]

    edited = client.post(
        f"/api/rooms/{room['id']}/messages/{original['id']}/edit",
        json={"content": "replacement wording, wrap up."},
    ).json()
    replacement_reply = _agent_msgs(edited["messages"])[0]

    rooms = client.get("/api/rooms").json()
    listed = next(r for r in rooms if r["id"] == room["id"])
    assert listed["last_message"]["content"] == replacement_reply["content"]
    assert listed["last_message"]["content"] != first_reply["content"]


def test_superseded_messages_are_removed_from_future_agent_history(client):
    room = make_room(client, "EditHistoryBank")

    posted = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "first draft should be ignored and wrap up."},
    ).json()
    original = _human_msgs(posted["messages"])[0]

    client.post(
        f"/api/rooms/{room['id']}/messages/{original['id']}/edit",
        json={"content": "second draft should remain and wrap up."},
    )

    orchestrator = client.app.state.orchestrator

    async def run() -> None:
        async with get_sessionmaker()() as session:
            room_row = await session.get(Room, room["id"])
            turns = await orchestrator._history_as_turns(session, room_row, "fce")
            compiled = "\n".join(turn.content for turn in turns)
            assert "second draft should remain and wrap up." in compiled
            assert "first draft should be ignored and wrap up." not in compiled

    client.portal.call(run)


def test_ws_receives_message_edited_event(client):
    room = make_room(client, "EditWsBank")
    posted = client.post(
        f"/api/rooms/{room['id']}/messages", json={"content": "hello, wrap up."}
    ).json()
    original = _human_msgs(posted["messages"])[0]

    with client.websocket_connect(f"/ws/rooms/{room['id']}") as ws:
        resp = client.post(
            f"/api/rooms/{room['id']}/messages/{original['id']}/edit",
            json={"content": "edited, wrap up."},
        )
        assert resp.status_code == 200
        body = resp.json()

        event = None
        for _ in range(12):
            candidate = ws.receive_json()
            if candidate.get("type") == "message_edited":
                event = candidate
                break

        assert event is not None
        assert event["message_id"] == original["id"]
        assert event["replacement_message_id"] == _human_msgs(body["messages"])[0]["id"]
        assert set(event["superseded_message_ids"]) == set(body["superseded_message_ids"])