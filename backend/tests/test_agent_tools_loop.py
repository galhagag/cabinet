"""Tool-calling round-trip inside a single turn: a tool call never consumes
an extra cycle, invocations are recorded on the final Message, execution
failures degrade gracefully, and a runaway tool loop is capped."""
from sqlalchemy import select

from app.agents.foundry_client import LLMResult, ToolCall
from app.agents.tools import ToolExecutionError
from app.db.base import get_sessionmaker
from app.db.models import AuditLog

from .conftest import make_room


class _ToolCallingLLM:
    """First call requests one tool; once the tool result is fed back,
    returns final text with an immediate hand-off so the autonomous loop
    doesn't keep going past this one turn."""

    def __init__(self, tool_name: str = "web_search") -> None:
        self._tool_name = tool_name

    async def complete(self, *, agent_key, system_prompt, turns, tools=None):
        already_used = any(t.tool_results for t in turns)
        if not already_used:
            return LLMResult(
                text="",
                tool_calls=[ToolCall(id="call-1", name=self._tool_name, arguments={"query": "x"})],
            )
        return LLMResult(
            text=f"[{agent_key}] final answer HANDOFF_TO_HUMAN",
            input_tokens=3,
            output_tokens=2,
        )


class _AlwaysToolCallingLLM:
    """Always requests a tool, never producing final text on its own —
    exercises the per-turn tool-round cap."""

    def __init__(self) -> None:
        self.calls_with_tools = 0
        self.calls_without_tools = 0

    async def complete(self, *, agent_key, system_prompt, turns, tools=None):
        if tools:
            self.calls_with_tools += 1
            return LLMResult(
                text="",
                tool_calls=[
                    ToolCall(id=f"call-{self.calls_with_tools}", name="web_search", arguments={"query": "x"})
                ],
            )
        self.calls_without_tools += 1
        return LLMResult(text="final forced answer", input_tokens=1, output_tokens=1)


class _FailingToolRunner:
    async def run(self, name, arguments, ctx):
        raise ToolExecutionError("simulated tool failure")


class _SucceedingToolRunner:
    """Stands in for the real ToolRunner so a successful round-trip is
    deterministic and network-free — the real web_search/drive_search
    executors make genuine outbound HTTP calls, which would otherwise make
    this test depend on network access and real third-party credentials."""

    async def run(self, name, arguments, ctx):
        return "stub tool result"


def test_tool_round_trip_records_invocations_and_final_text(client):
    room = make_room(client, "ToolLoopBank1")
    client.app.state.orchestrator._llm = _ToolCallingLLM()
    client.app.state.orchestrator._tool_runner = _SucceedingToolRunner()

    resp = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "@fce use tools please"},
    )
    assert resp.status_code == 200
    body = resp.json()
    agent_msg = next(m for m in body["messages"] if m["sender_type"] == "agent")
    assert "final answer" in agent_msg["content"]
    assert agent_msg["tool_invocations"] == [{"tool": "web_search", "query": "x"}]


def test_tool_round_trip_does_not_consume_extra_cycle(client):
    room = make_room(client, "ToolLoopBank2")
    client.app.state.orchestrator._llm = _ToolCallingLLM()
    client.app.state.orchestrator._tool_runner = _SucceedingToolRunner()

    resp = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "use tools please"},
    )
    assert resp.status_code == 200
    assert resp.json()["cycles_used"] == 1


def test_tool_execution_failure_feeds_error_back_without_pausing_room(client):
    room = make_room(client, "ToolLoopBank3")
    client.app.state.orchestrator._llm = _ToolCallingLLM()
    client.app.state.orchestrator._tool_runner = _FailingToolRunner()

    resp = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "@fce use tools please"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["room_status"] == "active"
    agent_msg = next(m for m in body["messages"] if m["sender_type"] == "agent")
    assert "final answer" in agent_msg["content"]

    async def fetch_audit():
        async with get_sessionmaker()() as session:
            result = await session.execute(
                select(AuditLog).where(AuditLog.action == "tool_invoked")
            )
            return result.scalars().all()

    rows = client.portal.call(fetch_audit)
    assert len(rows) == 1
    assert rows[0].detail["success"] is False


def test_runaway_tool_loop_is_capped_then_forced_to_final_text(client):
    room = make_room(client, "ToolLoopBank4")
    fake = _AlwaysToolCallingLLM()
    client.app.state.orchestrator._llm = fake
    client.app.state.orchestrator._tool_runner = _SucceedingToolRunner()

    resp = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "@fce use tools please"},
    )
    assert resp.status_code == 200
    max_rounds = client.app.state.settings.max_tool_rounds
    assert fake.calls_with_tools == max_rounds
    assert fake.calls_without_tools == 1
    agent_msg = next(m for m in resp.json()["messages"] if m["sender_type"] == "agent")
    assert agent_msg["content"] == "final forced answer"
