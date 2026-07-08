"""LLM backend: Claude via Microsoft Foundry (Azure AI), plus a deterministic mock.

Production path uses the official ``AsyncAnthropicFoundry`` client from the
``anthropic`` SDK — Messages API surface — authenticated with an Azure AI API
key resolved through the SecretProvider (Azure Key Vault in prod) or with
Microsoft Entra ID via ``azure_ad_token_provider``.

``CABINET_LLM_MODE=mock`` selects MockLLM: deterministic, domain-flavored
replies that exercise the full orchestration path (loop budget, mentions,
handoffs) with zero network and zero credentials.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..config import Settings
from .profiles import DATA_EXPERT_KEY, FCE_KEY


@dataclass(frozen=True)
class ChatTurn:
    role: str  # "user" | "assistant"
    content: str


class LLMBackend(Protocol):
    async def complete(
        self, *, agent_key: str, system_prompt: str, turns: list[ChatTurn]
    ) -> str: ...


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

    async def complete(
        self, *, agent_key: str, system_prompt: str, turns: list[ChatTurn]
    ) -> str:
        last = turns[-1].content if turns else ""
        flavor = self._FLAVOR.get(agent_key, "Acknowledged.")
        reply = f"[{agent_key}·mock] {flavor} (re: {last[:80]})"
        if "wrap up" in last.lower():
            reply += " HANDOFF_TO_HUMAN"
        return reply


class FoundryLLM:
    """Claude on Microsoft Foundry through the official SDK client."""

    def __init__(self, settings: Settings, api_key: str | None = None) -> None:
        from anthropic import AsyncAnthropicFoundry

        self._settings = settings
        kwargs: dict = {"resource": settings.foundry_resource}
        if settings.foundry_auth == "entra":
            # Microsoft Entra ID authentication (managed identity / workload
            # identity on ACA/AKS). azure-identity is a production-only dep.
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider

            kwargs["azure_ad_token_provider"] = get_bearer_token_provider(
                DefaultAzureCredential(),
                "https://cognitiveservices.azure.com/.default",
            )
        else:
            kwargs["api_key"] = api_key
        self._client = AsyncAnthropicFoundry(**kwargs)

    async def complete(
        self, *, agent_key: str, system_prompt: str, turns: list[ChatTurn]
    ) -> str:
        response = await self._client.messages.create(
            model=self._settings.foundry_model,
            max_tokens=self._settings.agent_max_tokens,
            system=system_prompt,
            messages=[{"role": t.role, "content": t.content} for t in turns],
        )
        # Refusal fallbacks aren't server-side on Foundry — degrade politely.
        if response.stop_reason == "refusal":
            return (
                "I can't help with that request as phrased. "
                "Could a human colleague rephrase or narrow the ask? "
                "HANDOFF_TO_HUMAN"
            )
        return "".join(
            block.text for block in response.content if block.type == "text"
        )


async def build_llm_backend(settings: Settings, secret_provider) -> LLMBackend:
    """Factory selecting the backend from configuration."""
    if settings.llm_mode == "foundry":
        api_key = None
        if settings.foundry_auth != "entra":
            api_key = await secret_provider.get_secret(settings.foundry_api_key_secret)
        return FoundryLLM(settings, api_key=api_key)
    return MockLLM()
