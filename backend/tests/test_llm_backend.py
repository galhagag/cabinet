"""LLM backend factory: mode dispatch and the azure_openai (GPT-on-Foundry) backend.

Exercises the real code paths (factory dispatch, response parsing) — only the
HTTPS hop to Azure is replaced with httpx.MockTransport, the same approach
test_entra_auth.py and test_gdrive_oauth.py use for their own external calls.
"""
import asyncio
import logging

import httpx

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
