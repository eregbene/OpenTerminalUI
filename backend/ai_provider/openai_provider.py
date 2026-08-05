from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from decimal import Decimal
from typing import AsyncIterator

from backend.ai_provider.base import AIProvider, ProviderRequest, ProviderResponse, ProviderStreamEvent
from backend.ai_provider.pricing import pricing_table
from backend.ai_provider.resilience import circuit_breaker, retry_delay
from backend.ai_provider.token_budget import estimate_tokens
from backend.ai_secrets import secret_registry


class OpenAIProvider(AIProvider):
    name = "openai"

    def __init__(self) -> None:
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.default_model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        self.retries = int(os.getenv("OPENAI_RETRIES", "2"))

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        api_key = secret_registry.get_secret(self.name, "api_key")
        if not api_key:
            return _local_fallback(request, provider=self.name, reason="missing_api_key")
        started = time.perf_counter()
        try:
            circuit_breaker.before_request(self.name, configured=True)
        except RuntimeError as exc:
            return _local_fallback(request, provider=self.name, reason=str(exc).replace(" ", "_"))
        payload = {
            "model": request.model or self.default_model,
            "messages": [{"role": "user", "content": request.prompt}],
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        last_error = "provider unavailable"
        for attempt in range(self.retries + 1):
            try:
                data = await asyncio.wait_for(asyncio.to_thread(self._post_chat, payload, api_key), timeout=request.timeout_seconds)
                choice = (data.get("choices") or [{}])[0]
                usage = data.get("usage") or {}
                prompt_tokens = _non_negative_int(usage.get("prompt_tokens"), estimate_tokens(request.prompt))
                completion_tokens = _non_negative_int(usage.get("completion_tokens"), 0)
                cached_tokens = _non_negative_int((usage.get("prompt_tokens_details") or {}).get("cached_tokens"), 0)
                reasoning_tokens = _non_negative_int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens"), 0)
                estimated_cost, currency, pricing_version = pricing_table.estimate(
                    provider=self.name,
                    model=str(data.get("model") or payload["model"]),
                    input_tokens=prompt_tokens,
                    output_tokens=completion_tokens,
                    cached_input_tokens=cached_tokens,
                    reasoning_tokens=reasoning_tokens,
                )
                latency_ms = (time.perf_counter() - started) * 1000
                circuit_breaker.success(self.name, latency_ms=latency_ms)
                return ProviderResponse(
                    text=str((choice.get("message") or {}).get("content") or ""),
                    provider=self.name,
                    model=str(data.get("model") or payload["model"]),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cached_tokens=cached_tokens,
                    reasoning_tokens=reasoning_tokens,
                    total_tokens=_non_negative_int(usage.get("total_tokens"), prompt_tokens + completion_tokens + reasoning_tokens),
                    usage_reported=bool(usage),
                    retry_count=attempt,
                    provider_request_count=attempt + 1,
                    latency_ms=latency_ms,
                    estimated_cost=estimated_cost,
                    confirmed_cost=str(usage.get("total_cost")) if _valid_decimal(usage.get("total_cost")) else None,
                    currency=currency,
                    pricing_version=pricing_version,
                    finish_reason=str(choice.get("finish_reason") or "stop"),
                )
            except (urllib.error.URLError, TimeoutError, asyncio.TimeoutError, ValueError) as exc:
                last_error = exc.__class__.__name__
                if attempt < self.retries:
                    await asyncio.sleep(retry_delay(attempt))
        circuit_breaker.failure(self.name, reason=last_error)
        return _local_fallback(request, provider=self.name, reason=last_error)

    async def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderStreamEvent]:
        api_key = secret_registry.get_secret(self.name, "api_key")
        yield ProviderStreamEvent("started", {"provider": self.name, "model": request.model or self.default_model, "provisional": True})
        if not api_key:
            response = _local_fallback(request, provider=self.name, reason="missing_api_key")
            yield ProviderStreamEvent("fallback", response.metadata | {"text": response.text})
            yield ProviderStreamEvent("completed", {"text": response.text, "usage": _usage_dict(response)})
            return
        started = time.perf_counter()
        payload = {
            "model": request.model or self.default_model,
            "messages": [{"role": "user", "content": request.prompt}],
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        first_token_at: float | None = None
        text_parts: list[str] = []
        usage: dict = {}
        try:
            circuit_breaker.before_request(self.name, configured=True)
            async for chunk in self._stream_chat(payload, api_key, request.timeout_seconds):
                if chunk.get("usage"):
                    usage = chunk["usage"]
                    yield ProviderStreamEvent("usage", usage)
                    continue
                delta = (((chunk.get("choices") or [{}])[0]).get("delta") or {}).get("content")
                if not delta:
                    continue
                if first_token_at is None:
                    first_token_at = time.perf_counter()
                text_parts.append(str(delta))
                yield ProviderStreamEvent("provisional_delta", {"text": str(delta), "provisional": True})
            latency_ms = (time.perf_counter() - started) * 1000
            circuit_breaker.success(self.name, latency_ms=latency_ms)
            response = self._response_from_stream(request, "".join(text_parts), usage, started, first_token_at)
            yield ProviderStreamEvent("completed", {"text": response.text, "usage": _usage_dict(response), "provisional": False})
        except Exception as exc:
            circuit_breaker.failure(self.name, reason=exc.__class__.__name__)
            response = _local_fallback(request, provider=self.name, reason=exc.__class__.__name__)
            yield ProviderStreamEvent("warning", {"message": "provider stream interrupted"})
            yield ProviderStreamEvent("fallback", response.metadata | {"text": response.text})
            yield ProviderStreamEvent("completed", {"text": response.text, "usage": _usage_dict(response), "provisional": False})

    def _post_chat(self, payload: dict, api_key: str) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    async def _stream_chat(self, payload: dict, api_key: str, timeout_seconds: float) -> AsyncIterator[dict]:
        def _open():
            req = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                method="POST",
            )
            return urllib.request.urlopen(req, timeout=timeout_seconds)

        resp = await asyncio.to_thread(_open)
        try:
            while True:
                line = await asyncio.to_thread(resp.readline)
                if not line:
                    break
                decoded = line.decode("utf-8").strip()
                if not decoded.startswith("data:"):
                    continue
                data = decoded[5:].strip()
                if data == "[DONE]":
                    break
                yield json.loads(data)
        finally:
            resp.close()

    def _response_from_stream(self, request: ProviderRequest, text: str, usage: dict, started: float, first_token_at: float | None) -> ProviderResponse:
        prompt_tokens = _non_negative_int(usage.get("prompt_tokens"), estimate_tokens(request.prompt))
        completion_tokens = _non_negative_int(usage.get("completion_tokens"), estimate_tokens(text))
        cached_tokens = _non_negative_int((usage.get("prompt_tokens_details") or {}).get("cached_tokens"), 0)
        reasoning_tokens = _non_negative_int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens"), 0)
        estimated_cost, currency, pricing_version = pricing_table.estimate(
            provider=self.name,
            model=request.model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            cached_input_tokens=cached_tokens,
            reasoning_tokens=reasoning_tokens,
        )
        now = time.perf_counter()
        return ProviderResponse(
            text=text,
            provider=self.name,
            model=request.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            reasoning_tokens=reasoning_tokens,
            total_tokens=_non_negative_int(usage.get("total_tokens"), prompt_tokens + completion_tokens + reasoning_tokens),
            usage_reported=bool(usage),
            latency_ms=(now - started) * 1000,
            time_to_first_token_ms=((first_token_at - started) * 1000) if first_token_at else None,
            stream_duration_ms=(now - started) * 1000,
            estimated_cost=estimated_cost,
            currency=currency,
            pricing_version=pricing_version,
        )


def _local_fallback(request: ProviderRequest, *, provider: str, reason: str) -> ProviderResponse:
    text = "I don't have sufficient verified evidence to answer."
    estimated_cost, currency, pricing_version = pricing_table.estimate(provider=provider, model=request.model, input_tokens=estimate_tokens(request.prompt), output_tokens=estimate_tokens(text))
    return ProviderResponse(
        text=text,
        provider=provider,
        model=request.model,
        prompt_tokens=estimate_tokens(request.prompt),
        completion_tokens=estimate_tokens(text),
        total_tokens=estimate_tokens(request.prompt) + estimate_tokens(text),
        latency_ms=0.0,
        estimated_cost=estimated_cost,
        currency=currency,
        pricing_version=pricing_version,
        finish_reason=f"fallback:{reason}",
        metadata={"fallback_reason": reason},
    )


def _non_negative_int(value, fallback: int) -> int:
    if value is None:
        return fallback
    parsed = int(value)
    if parsed < 0:
        raise ValueError("negative provider usage")
    return parsed


def _valid_decimal(value) -> bool:
    if value is None:
        return False
    try:
        return Decimal(str(value)) >= 0
    except Exception:
        return False


def _usage_dict(response: ProviderResponse) -> dict:
    return {
        "prompt_tokens": response.prompt_tokens,
        "completion_tokens": response.completion_tokens,
        "cached_tokens": response.cached_tokens,
        "reasoning_tokens": response.reasoning_tokens,
        "total_tokens": response.total_tokens,
        "usage_reported": response.usage_reported,
        "estimated_cost": response.estimated_cost,
        "confirmed_cost": response.confirmed_cost,
        "currency": response.currency,
        "pricing_version": response.pricing_version,
    }
