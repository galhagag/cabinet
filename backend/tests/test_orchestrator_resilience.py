"""LLM-failure crash safety in the autonomous loop (Design 02 Stage 1 / C2).

Today, an LLM failure mid-loop 500s the request after the cycle was already
claimed, leaving the room ACTIVE at an exhausted budget forever — no agent
ever speaks again and /resume returns 409. This must instead pause the room
with a visible system notice so /resume works.
"""
from app.agents.foundry_client import LLMResult

from .conftest import make_room


class _FlakyLLM:
    """Succeeds `ok_calls` times, then raises LLMError on every call after."""

    def __init__(self, ok_calls: int) -> None:
        self._ok_calls = ok_calls
        self._calls = 0

    async def complete(self, *, agent_key, system_prompt, turns, tools=None):
        from app.agents.foundry_client import LLMError

        self._calls += 1
        if self._calls > self._ok_calls:
            raise LLMError("simulated upstream failure")
        return LLMResult(text=f"[{agent_key}] turn {self._calls}", input_tokens=1, output_tokens=1)


def test_llm_failure_mid_loop_pauses_room_and_allows_resume(client):
    room = make_room(client, "FlakyBank")
    client.app.state.orchestrator._llm = _FlakyLLM(ok_calls=2)

    resp = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "Please plan the full onboarding together."},
    )
    assert resp.status_code == 200
    body = resp.json()

    kinds = [m["sender_type"] for m in body["messages"]]
    assert kinds.count("agent") == 2, "two clean turns before the simulated failure"
    assert kinds.count("system") == 1, "a system notice must record the failure"
    assert body["room_status"] == "paused_awaiting_human"

    # The core bug: today this 409s because nothing ever paused the room.
    resume = client.get(f"/api/rooms/{room['id']}").json()
    assert resume["status"] == "paused_awaiting_human"
    resume_resp = client.post(f"/api/rooms/{room['id']}/resume")
    assert resume_resp.status_code == 200


def test_llm_failure_on_first_turn_still_pauses_and_resumes(client):
    room = make_room(client, "InstaFlakyBank")
    client.app.state.orchestrator._llm = _FlakyLLM(ok_calls=0)

    resp = client.post(f"/api/rooms/{room['id']}/messages", json={"content": "go"})
    assert resp.status_code == 200
    body = resp.json()
    assert [m["sender_type"] for m in body["messages"]] == ["human", "system"]
    assert body["room_status"] == "paused_awaiting_human"
    assert client.post(f"/api/rooms/{room['id']}/resume").status_code == 200


def test_llm_failure_on_mention_reply_pauses_room_and_allows_resume(client):
    """_run_mention_reply had the same unguarded `self._llm.complete(...)` call
    as the autonomous loop, but was not covered by the Stage 1 fix — an
    @-mention that hit a flaky LLM would 500 the request instead of pausing
    the room. This must degrade exactly like the autonomous-loop path.
    """
    room = make_room(client, "MentionFlakyBank")
    client.app.state.orchestrator._llm = _FlakyLLM(ok_calls=0)

    resp = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "@data_expert please map the schema"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [m["sender_type"] for m in body["messages"]] == ["human", "system"]
    assert body["room_status"] == "paused_awaiting_human"
    assert client.post(f"/api/rooms/{room['id']}/resume").status_code == 200
