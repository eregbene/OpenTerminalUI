from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol


@dataclass(frozen=True)
class ProviderRequest:
    prompt: str
    model: str
    max_tokens: int
    temperature: float
    timeout_seconds: float
    provider_request_id: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: float = 0.0
    time_to_first_token_ms: float | None = None
    stream_duration_ms: float | None = None
    retry_count: int = 0
    provider_request_count: int = 1
    total_tokens: int = 0
    reasoning_tokens: int = 0
    usage_reported: bool = False
    estimated_cost: str = "0"
    confirmed_cost: str | None = None
    currency: str = "USD"
    pricing_version: str = "local-estimate-v1"
    cancelled: bool = False
    finish_reason: str = "stop"
    citations: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderStreamEvent:
    type: str
    data: dict[str, Any]


class AIProvider(Protocol):
    name: str

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        ...

    async def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderStreamEvent]:
        ...
