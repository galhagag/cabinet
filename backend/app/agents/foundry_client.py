"""LLM backend: Claude via Microsoft Foundry (Azure AI), plus a deterministic mock.

Production path uses the official ``AsyncAnthropicFoundry`` client from the
``anthropic`` SDK — Messages API surface — authenticated with an Azure AI API
key resolved through the SecretProvider (Azure Key Vault in prod) or with
Microsoft Entra ID via ``azure_ad_token_provider``.

``CABINET_LLM_MODE=azure_openai`` selects AzureOpenAILLM — GPT models on the
same kind of Microsoft Foundry resource, via the official ``openai`` SDK's
``AsyncAzureOpenAI`` client (Chat Completions API), authenticated the same
two ways as Foundry: an Azure AI API key or Microsoft Entra ID.

``CABINET_LLM_MODE=mock`` selects MockLLM: deterministic, domain-flavored
replies that exercise the full orchestration path (loop budget, mentions,
handoffs) with zero network and zero credentials.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Protocol

import httpx

from ..config import Settings
from .profiles import DATA_EXPERT_KEY, FCE_KEY

logger = logging.getLogger(__name__)

# Strips a *nested* mock tag out of quoted history — without this, an agent
# replying right after another mock agent echoes that agent's own
# "[key·mock]" tag back inside its own reply.
_MOCK_TAG_RE = re.compile(r"\[[\w.]+·mock\]\s*")


@dataclass(frozen=True)
class ChatTurn:
    role: str  # "user" | "assistant"
    content: str


@dataclass(frozen=True)
class LLMResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class LLMError(Exception):
    """The LLM backend failed to produce a completion (timeout, API error,
    refusal-that-isn't-handled-server-side, etc.). Callers must treat this as
    recoverable: pause the room, never leave it stranded active."""


class LLMBackend(Protocol):
    async def complete(
        self, *, agent_key: str, system_prompt: str, turns: list[ChatTurn]
    ) -> LLMResult: ...


class MockLLM:
    """Deterministic scripted agents for dev/CI.

    Replies embed the agent's domain vocabulary and echo a marker so tests
    can assert routing. Any turn containing "wrap up" triggers the
    HANDOFF_TO_HUMAN completion token to exercise early-exit.
    """

    _FLAVOR = {
        DATA_EXPERT_KEY: (
            "From the data side: I'll validate the Parquet ingestion layout, "
            "map source columns to the canonical schema, and register the "
            "feature catalog in MLFlow."
        ),
        FCE_KEY: (
            "From the compliance side: I'll define the 6-month rolling window "
            "metrics, credit-transaction rules and country whitelist, and map "
            "the 1LOD/2LOD investigation workflow states."
        ),
    }

    @staticmethod
    def _quote(text: str, limit: int = 80) -> str:
        """Short, clean back-reference for the ``(re: ...)`` echo.

        Strips any nested "[key·mock]" tag so an untagged agent reply doesn't
        end up quoting another agent's own tagged message, collapses merged
        multi-message turns onto one line, and truncates on a word boundary
        instead of a raw character slice so long quotes don't get cut mid-word.
        """
        quoted = _MOCK_TAG_RE.sub("", text).replace("\n", " ").strip()
        if len(quoted) > limit:
            quoted = quoted[:limit].rsplit(" ", 1)[0] + "…"
        return quoted

    async def complete(
        self, *, agent_key: str, system_prompt: str, turns: list[ChatTurn]
    ) -> LLMResult:
        last = turns[-1].content if turns else ""
        flavor = self._FLAVOR.get(agent_key, "Acknowledged.")
        reply = f"[{agent_key}·mock] {flavor} (re: {self._quote(last)})"
        if "wrap up" in last.lower():
            reply += " HANDOFF_TO_HUMAN"
        # ~4 chars/token — a rough but deterministic stand-in for real usage,
        # good enough to exercise the token-usage UI in dev/CI.
        prompt_chars = len(system_prompt) + sum(len(t.content) for t in turns)
        return LLMResult(
            text=reply,
            input_tokens=max(1, prompt_chars // 4),
            output_tokens=max(1, len(reply) // 4),
        )


class FoundryLLM:
    """Claude on Microsoft Foundry through the official SDK client."""

    def __init__(self, settings: Settings, api_key: str | None = None) -> None:
        from anthropic import AsyncAnthropicFoundry

        self._settings = settings
        kwargs: dict = {"resource": settings.foundry_resource}
        if settings.foundry_auth == "entra":
            # Microsoft Entra ID authentication (managed identity / workload
            # identity on ACA/AKS). azure-identity is a production-only dep.
            # aio credential: token acquisition must not block the event loop.
            from azure.identity.aio import (
                DefaultAzureCredential,
                get_bearer_token_provider,
            )

            kwargs["azure_ad_token_provider"] = get_bearer_token_provider(
                DefaultAzureCredential(),
                "https://cognitiveservices.azure.com/.default",
            )
        else:
            kwargs["api_key"] = api_key
        self._client = AsyncAnthropicFoundry(**kwargs)

    async def complete(
        self, *, agent_key: str, system_prompt: str, turns: list[ChatTurn]
    ) -> LLMResult:
        try:
            response = await self._client.messages.create(
                model=self._settings.foundry_model,
                max_tokens=self._settings.agent_max_tokens,
                system=system_prompt,
                messages=[{"role": t.role, "content": t.content} for t in turns],
            )
        except Exception as exc:
            raise LLMError(f"Foundry completion failed for {agent_key}: {exc}") from exc
        input_tokens = getattr(response.usage, "input_tokens", 0) or 0
        output_tokens = getattr(response.usage, "output_tokens", 0) or 0

        # Refusal fallbacks aren't server-side on Foundry — degrade politely.
        if response.stop_reason == "refusal":
            return LLMResult(
                text=(
                    "I can't help with that request as phrased. "
                    "Could a human colleague rephrase or narrow the ask? "
                    "HANDOFF_TO_HUMAN"
                ),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        return LLMResult(text=text, input_tokens=input_tokens, output_tokens=output_tokens)


class AzureOpenAILLM:
    """GPT models on Microsoft Foundry through the official Azure OpenAI SDK."""

    def __init__(
        self,
        settings: Settings,
        api_key: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        from openai import AsyncAzureOpenAI

        self._settings = settings
        kwargs: dict = {
            "azure_endpoint": settings.azure_openai_endpoint,
            "api_version": settings.azure_openai_api_version,
        }
        if http_client is not None:
            kwargs["http_client"] = http_client
        if settings.azure_openai_auth == "entra":
            from azure.identity.aio import (
                DefaultAzureCredential,
                get_bearer_token_provider,
            )

            kwargs["azure_ad_token_provider"] = get_bearer_token_provider(
                DefaultAzureCredential(),
                "https://cognitiveservices.azure.com/.default",
            )
        else:
            kwargs["api_key"] = api_key
        self._client = AsyncAzureOpenAI(**kwargs)

    async def complete(
        self, *, agent_key: str, system_prompt: str, turns: list[ChatTurn]
    ) -> LLMResult:
        messages = [{"role": "system", "content": system_prompt}] + [
            {"role": t.role, "content": t.content} for t in turns
        ]
        try:
            response = await self._client.chat.completions.create(
                model=self._settings.azure_openai_deployment,
                max_completion_tokens=self._settings.agent_max_tokens,
                messages=messages,
            )
        except Exception as exc:
            raise LLMError(f"Azure OpenAI completion failed for {agent_key}: {exc}") from exc
        choice = response.choices[0]
        usage = response.usage
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0

        # Content filtering is enforced server-side and returns no message
        # text — degrade the same way FoundryLLM does for a Claude refusal.
        if choice.finish_reason == "content_filter":
            return LLMResult(
                text=(
                    "I can't help with that request as phrased. "
                    "Could a human colleague rephrase or narrow the ask? "
                    "HANDOFF_TO_HUMAN"
                ),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        return LLMResult(
            text=choice.message.content or "",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


async def build_llm_backend(settings: Settings, secret_provider) -> LLMBackend:
    """Factory selecting the backend from configuration."""
    if settings.llm_mode == "foundry":
        api_key = None
        if settings.foundry_auth != "entra":
            api_key = await secret_provider.get_secret(settings.foundry_api_key_secret)
        return FoundryLLM(settings, api_key=api_key)
    if settings.llm_mode == "azure_openai":
        api_key = None
        if settings.azure_openai_auth != "entra":
            api_key = await secret_provider.get_secret(
                settings.azure_openai_api_key_secret
            )
        return AzureOpenAILLM(settings, api_key=api_key)
    if settings.llm_mode != "mock":
        logger.warning(
            "CABINET_LLM_MODE=%r is not a recognized LLM backend (expected "
            "'mock', 'foundry', or 'azure_openai') — no LLM connection is "
            "configured; falling back to MockLLM.",
            settings.llm_mode,
        )
    return MockLLM()
