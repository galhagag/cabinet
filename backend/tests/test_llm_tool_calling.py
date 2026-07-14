"""Common tool-calling vocabulary (ToolSpec/ToolCall/ToolResult/ChatTurn) and
MockLLM's deterministic scripted tool-call trigger — exercises the full
round trip with zero network calls."""
import asyncio

from app.agents.foundry_client import ChatTurn, MockLLM, ToolCall, ToolResult, ToolSpec


def _run(coro):
    return asyncio.run(coro)


_WEB_SEARCH = ToolSpec(
    name="web_search",
    description="Search the web.",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
)


def test_mock_llm_requests_a_tool_when_tools_are_offered_and_triggered():
    backend = MockLLM()
    result = _run(
        backend.complete(
            agent_key="fce",
            system_prompt="You are helpful.",
            turns=[ChatTurn(role="user", content="please use tools to check this")],
            tools=[_WEB_SEARCH],
        )
    )
    assert result.tool_calls == [
        ToolCall(id="mock-call-1", name="web_search", arguments={"query": "mock query"})
    ]


def test_mock_llm_does_not_request_a_tool_without_the_trigger_phrase():
    backend = MockLLM()
    result = _run(
        backend.complete(
            agent_key="fce",
            system_prompt="You are helpful.",
            turns=[ChatTurn(role="user", content="just a normal message")],
            tools=[_WEB_SEARCH],
        )
    )
    assert result.tool_calls is None


def test_mock_llm_ignores_the_trigger_phrase_when_no_tools_are_offered():
    backend = MockLLM()
    result = _run(
        backend.complete(
            agent_key="fce",
            system_prompt="You are helpful.",
            turns=[ChatTurn(role="user", content="please use tools to check this")],
            tools=None,
        )
    )
    assert result.tool_calls is None


def test_mock_llm_returns_final_text_after_a_tool_result_is_fed_back():
    backend = MockLLM()
    result = _run(
        backend.complete(
            agent_key="fce",
            system_prompt="You are helpful.",
            turns=[
                ChatTurn(role="user", content="please use tools to check this"),
                ChatTurn(
                    role="assistant",
                    content="",
                    tool_calls=[
                        ToolCall(id="mock-call-1", name="web_search", arguments={"query": "mock query"})
                    ],
                ),
                ChatTurn(
                    role="user",
                    content="",
                    tool_results=[ToolResult(tool_call_id="mock-call-1", content="some result")],
                ),
            ],
            tools=[_WEB_SEARCH],
        )
    )
    assert result.tool_calls is None
    assert result.text != ""
