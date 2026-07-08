"""Agent-to-agent loop control: hard 6-cycle budget, pause, human resume."""
from .conftest import make_room


def _agent_msgs(messages):
    return [m for m in messages if m["sender_type"] == "agent"]


def test_autonomous_loop_halts_at_exactly_six_cycles(client):
    room = make_room(client, "LoopBank")
    resp = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "Please plan the full onboarding together."},
    )
    assert resp.status_code == 200
    body = resp.json()

    agent_msgs = _agent_msgs(body["messages"])
    assert len(agent_msgs) == 6, "budget must cap the exchange at 6 agent turns"
    assert [m["cycle_number"] for m in agent_msgs] == [1, 2, 3, 4, 5, 6]
    # Agents alternate: no agent speaks twice in a row.
    speakers = [m["agent_key"] for m in agent_msgs]
    assert all(a != b for a, b in zip(speakers, speakers[1:]))

    assert body["room_status"] == "paused_awaiting_human"
    assert body["cycles_used"] == 6
    assert body["cycle_limit"] == 6


def test_paused_room_locks_agents_until_human_posts(client):
    room = make_room(client, "PausedBank")
    client.post(f"/api/rooms/{room['id']}/messages", json={"content": "go"})
    assert client.get(f"/api/rooms/{room['id']}").json()["status"] == (
        "paused_awaiting_human"
    )

    # A new human message resets the budget, reactivates, and re-runs the loop.
    resp = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "Thanks — now focus on the data layer."},
    )
    body = resp.json()
    assert len(_agent_msgs(body["messages"])) == 6
    assert body["cycles_used"] == 6  # fresh budget consumed, not 12


def test_resume_endpoint_restarts_loop_after_pause(client):
    room = make_room(client, "ResumeBank")
    client.post(f"/api/rooms/{room['id']}/messages", json={"content": "go"})

    resp = client.post(f"/api/rooms/{room['id']}/resume")
    assert resp.status_code == 200
    body = resp.json()
    assert len(_agent_msgs(body["messages"])) == 6
    assert body["room_status"] == "paused_awaiting_human"

    # Resume on an active room is a conflict.
    room2 = make_room(client, "ActiveBank")
    assert client.post(f"/api/rooms/{room2['id']}/resume").status_code == 409


def test_handoff_token_ends_exchange_early(client):
    room = make_room(client, "HandoffBank")
    resp = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "Give a one-line status and wrap up."},
    )
    body = resp.json()
    agent_msgs = _agent_msgs(body["messages"])
    assert len(agent_msgs) == 1
    assert "HANDOFF_TO_HUMAN" in agent_msgs[0]["content"]
    assert body["room_status"] == "active"
    assert body["cycles_used"] == 1
