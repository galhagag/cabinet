"""A room's history can span a MockLLM -> real-backend transition (e.g. a
CABINET_LLM_MODE flip mid-project). Without stripping, a real model sees its
own past "assistant" turns literally start with "[key·mock]" and imitates
that as an established reply-prefix convention — producing genuine,
non-canned replies that nonetheless look mocked forever after.
"""
from app.agents.foundry_client import LLMResult
from app.db.base import get_sessionmaker
from app.db.models import Message

from .conftest import make_room


class _RecordingLLM:
    """Records every call's turns and returns a fixed, clean reply."""

    def __init__(self) -> None:
        self.calls: list[list] = []

    async def complete(self, *, agent_key, system_prompt, turns):
        self.calls.append(turns)
        return LLMResult(text="clean real reply", input_tokens=1, output_tokens=1)


def test_stale_mock_tag_in_own_history_is_stripped_before_replay(client):
    room = make_room(client, "PostMockFlipBank")

    async def seed_stale_mock_message():
        async with get_sessionmaker()() as session:
            session.add(
                Message(
                    room_id=room["id"],
                    sender_type="agent",
                    sender_name="Data Expert",
                    agent_key="data_expert",
                    content=(
                        "[data_expert·mock] From the data side: I'll validate "
                        "the Parquet ingestion layout."
                    ),
                )
            )
            await session.commit()

    client.portal.call(seed_stale_mock_message)

    recorder = _RecordingLLM()
    client.app.state.orchestrator._llm = recorder

    resp = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "@data_expert continue"},
    )
    assert resp.status_code == 200

    assistant_turns = [
        t.content for call in recorder.calls for t in call if t.role == "assistant"
    ]
    combined = "\n".join(assistant_turns)
    assert "[data_expert·mock]" not in combined
    assert "I'll validate the Parquet ingestion layout." in combined
