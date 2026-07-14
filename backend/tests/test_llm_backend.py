"""LLM backend factory: mode dispatch and the azure_openai (GPT-on-Foundry) backend.

Exercises the real code paths (factory dispatch, response parsing) — only the
HTTPS hop to Azure is replaced with httpx.MockTransport, the same approach
test_entra_auth.py and test_gdrive_oauth.py use for their own external calls.
"""
import asyncio
import logging

import httpx
import pytest

from app.agents.foundry_client import (
    AzureOpenAILLM,
    ChatTurn,
    MockLLM,
    build_llm_backend,
)
from app.config import Settings
from app.services.secrets import EnvSecretProvider


def _run(coro):
    return asyncio.run(coro)


def test_azure_openai_mode_builds_azure_openai_backend(monkeypatch):
    monkeypatch.setenv("CABINET_SECRET_AZURE_OPENAI_API_KEY", "test-key")
    settings = Settings(
        llm_mode="azure_openai",
        azure_openai_endpoint="https://example-resource.services.ai.azure.com/",
        azure_openai_deployment="gpt-5.4",
    )
    backend = _run(build_llm_backend(settings, EnvSecretProvider()))
    assert isinstance(backend, AzureOpenAILLM)


def test_unrecognized_llm_mode_warns_and_falls_back_to_mock(caplog):
    settings = Settings(llm_mode="gpt5")
    with caplog.at_level(logging.WARNING):
        backend = _run(build_llm_backend(settings, EnvSecretProvider()))
    assert isinstance(backend, MockLLM)
    assert "gpt5" in caplog.text
    assert "no llm connection" in caplog.text.lower()


def test_mock_mode_does_not_warn(caplog):
    settings = Settings(llm_mode="mock")
    with caplog.at_level(logging.WARNING):
        backend = _run(build_llm_backend(settings, EnvSecretProvider()))
    assert isinstance(backend, MockLLM)
    assert caplog.text == ""


def _chat_completion_handler(content="hello", finish_reason="stop"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )

    return handler


def _backend(handler) -> AzureOpenAILLM:
    settings = Settings(
        azure_openai_endpoint="https://example-resource.services.ai.azure.com/",
        azure_openai_deployment="gpt-5.4",
    )
    transport = httpx.MockTransport(handler)
    return AzureOpenAILLM(
        settings, api_key="test-key", http_client=httpx.AsyncClient(transport=transport)
    )


def test_azure_openai_complete_parses_response_text_and_usage():
    backend = _backend(_chat_completion_handler(content="Bonjour"))
    result = _run(
        backend.complete(
            agent_key="data_expert",
            system_prompt="You are helpful.",
            turns=[ChatTurn(role="user", content="hi")],
        )
    )
    assert result.text == "Bonjour"
    assert result.input_tokens == 10
    assert result.output_tokens == 5


def test_azure_openai_complete_degrades_politely_on_content_filter():
    backend = _backend(_chat_completion_handler(content=None, finish_reason="content_filter"))
    result = _run(
        backend.complete(
            agent_key="data_expert",
            system_prompt="You are helpful.",
            turns=[ChatTurn(role="user", content="hi")],
        )
    )
    assert "HANDOFF_TO_HUMAN" in result.text


def test_azure_openai_complete_wraps_sdk_errors_as_llmerror():
    from app.agents.foundry_client import LLMError

    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    backend = _backend(failing_handler)
    with pytest.raises(LLMError):
        _run(
            backend.complete(
                agent_key="data_expert",
                system_prompt="You are helpful.",
                turns=[ChatTurn(role="user", content="hi")],
            )
        )


def test_mock_reply_quote_does_not_leak_nested_tag_or_cut_mid_word():
    """Data Expert replying right after FCE (no human turn in between) must not
    echo FCE's already-tagged message verbatim — that nests a "[fce·mock]" tag
    inside Data Expert's own reply and, at the old fixed 80-char cutoff, slices
    mid-word ("6-month" -> "6-m"), making the reply look corrupted.
    """
    backend = MockLLM()
    fce_turn = (
        "Financial Crime Expert: [fce·mock] From the compliance side: I'll define "
        "the 6-month rolling window metrics, credit-transaction rules and country "
        "whitelist, and map the 1LOD/2LOD investigation workflow states."
    )
    result = _run(
        backend.complete(
            agent_key="data_expert",
            system_prompt="You are helpful.",
            turns=[ChatTurn(role="user", content=fce_turn)],
        )
    )
    assert "[fce·mock]" not in result.text
    assert "6-m)" not in result.text


from app.agents.foundry_client import FoundryLLM, ToolCall, ToolResult, ToolSpec


def _foundry_backend(handler) -> FoundryLLM:
    settings = Settings(foundry_resource="test-resource", foundry_model="claude-opus-4-8")
    transport = httpx.MockTransport(handler)
    return FoundryLLM(
        settings, api_key="test-key", http_client=httpx.AsyncClient(transport=transport)
    )


def _foundry_tool_use_handler():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "msg_test_tool",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-4-8",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "drive_search",
                        "input": {"query": "schema doc"},
                    }
                ],
                "stop_reason": "tool_use",
                "stop_sequence": None,
                "usage": {"input_tokens": 20, "output_tokens": 8},
            },
        )

    return handler


def _foundry_text_handler(text="Final answer"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "msg_test_final",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-4-8",
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 15, "output_tokens": 6},
            },
        )

    return handler


_DRIVE_SEARCH_SPEC = ToolSpec(
    name="drive_search",
    description="Search Drive.",
    parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
)


def test_foundry_complete_returns_tool_calls():
    backend = _foundry_backend(_foundry_tool_use_handler())
    result = _run(
        backend.complete(
            agent_key="data_expert",
            system_prompt="You are helpful.",
            turns=[ChatTurn(role="user", content="find the schema doc")],
            tools=[_DRIVE_SEARCH_SPEC],
        )
    )
    assert result.tool_calls == [
        ToolCall(id="toolu_1", name="drive_search", arguments={"query": "schema doc"})
    ]
    assert result.input_tokens == 20
    assert result.output_tokens == 8


def test_foundry_complete_replays_tool_result_and_returns_final_text():
    backend = _foundry_backend(_foundry_text_handler("Based on the doc, use column X."))
    result = _run(
        backend.complete(
            agent_key="data_expert",
            system_prompt="You are helpful.",
            turns=[
                ChatTurn(role="user", content="find the schema doc"),
                ChatTurn(
                    role="assistant",
                    content="",
                    tool_calls=[
                        ToolCall(id="toolu_1", name="drive_search", arguments={"query": "schema doc"})
                    ],
                ),
                ChatTurn(
                    role="user",
                    content="",
                    tool_results=[
                        ToolResult(tool_call_id="toolu_1", content="Found: Schema Mapping.docx")
                    ],
                ),
            ],
        )
    )
    assert result.text == "Based on the doc, use column X."
    assert result.tool_calls is None


def _tool_call_completion_handler():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test-tool",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "web_search",
                                        "arguments": '{"query": "AML rolling window"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
            },
        )

    return handler


def test_azure_openai_complete_returns_tool_calls():
    backend = _backend(_tool_call_completion_handler())
    result = _run(
        backend.complete(
            agent_key="fce",
            system_prompt="You are helpful.",
            turns=[ChatTurn(role="user", content="look this up")],
            tools=[
                ToolSpec(
                    name="web_search",
                    description="Search the web.",
                    parameters={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                )
            ],
        )
    )
    assert result.tool_calls == [
        ToolCall(id="call_1", name="web_search", arguments={"query": "AML rolling window"})
    ]
    assert result.input_tokens == 12
    assert result.output_tokens == 6


def test_azure_openai_complete_replays_tool_result_and_returns_final_text():
    backend = _backend(_chat_completion_handler(content="Based on the search, proceed."))
    result = _run(
        backend.complete(
            agent_key="fce",
            system_prompt="You are helpful.",
            turns=[
                ChatTurn(role="user", content="look this up"),
                ChatTurn(
                    role="assistant",
                    content="",
                    tool_calls=[
                        ToolCall(id="call_1", name="web_search", arguments={"query": "AML rolling window"})
                    ],
                ),
                ChatTurn(
                    role="user",
                    content="",
                    tool_results=[ToolResult(tool_call_id="call_1", content="Some search result")],
                ),
            ],
        )
    )
    assert result.text == "Based on the search, proceed."
    assert result.tool_calls is None
