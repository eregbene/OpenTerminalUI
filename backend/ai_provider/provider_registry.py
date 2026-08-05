from __future__ import annotations

import os

from backend.ai_provider.base import AIProvider, ProviderRequest, ProviderResponse, ProviderStreamEvent
from backend.ai_provider.openai_provider import OpenAIProvider, _local_fallback


class LocalProvider:
    name = "local"

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        return _local_fallback(request, provider=self.name, reason="local_stub")

    async def stream(self, request: ProviderRequest):
        response = await self.complete(request)
        yield ProviderStreamEvent("started", {"provider": self.name, "model": request.model, "provisional": True})
        yield ProviderStreamEvent("fallback", {"text": response.text, "reason": response.finish_reason})
        yield ProviderStreamEvent(
            "completed",
            {
                "text": response.text,
                "usage": {
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "total_tokens": response.total_tokens,
                    "estimated_cost": response.estimated_cost,
                    "currency": response.currency,
                    "pricing_version": response.pricing_version,
                },
                "provisional": False,
            },
        )


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, AIProvider] = {"openai": OpenAIProvider(), "local": LocalProvider()}

    def get(self, name: str | None = None) -> AIProvider:
        provider = (name or os.getenv("AI_PROVIDER", "openai")).strip().lower()
        return self._providers.get(provider) or self._providers["local"]

    def list(self) -> list[AIProvider]:
        return list(self._providers.values())


provider_registry = ProviderRegistry()
