from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from backend.intelligence.trading.config import AITradingConfig, ai_trading_config
from backend.intelligence.trading.models import ProviderTelemetry, TradeDecision, decision_schema


class OpenAITradeDecisionClient:
    def __init__(self, config: AITradingConfig | None = None) -> None:
        self.config = config or ai_trading_config()

    def provider_diagnostics(self) -> dict[str, Any]:
        return {
            "provider": "openai",
            "model_configured": bool(self.config.model),
            "model": self.config.model,
            "api_key_configured": self.config.api_key_configured,
        }

    async def health_check(self) -> dict[str, Any]:
        if not self.config.api_key_configured:
            return {"provider": "openai", "status": "missing_api_key", "api_key_configured": False}
        started = time.perf_counter()
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(self._health_responses_create),
                timeout=min(self.config.request_timeout_seconds, 10),
            )
            return {
                "provider": "openai",
                "status": "ok",
                "api_key_configured": True,
                "model": str(getattr(response, "model", self.config.model) or self.config.model),
                "request_id": str(getattr(response, "id", "") or "") or None,
                "latency_ms": (time.perf_counter() - started) * 1000,
            }
        except Exception as exc:
            return {
                "provider": "openai",
                "status": "provider_error",
                "api_key_configured": True,
                "model": self.config.model,
                "latency_ms": (time.perf_counter() - started) * 1000,
                "error_category": exc.__class__.__name__,
            }

    async def generate_trade_decision(self, context: dict[str, Any]) -> tuple[TradeDecision | None, ProviderTelemetry, str | None]:
        if not self.config.api_key_configured:
            return None, ProviderTelemetry(provider="openai", model=self.config.model, response_status="missing_api_key", error_category="missing_api_key"), None
        started = time.perf_counter()
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                response = await asyncio.wait_for(asyncio.to_thread(self._responses_create, context), timeout=self.config.request_timeout_seconds)
                text = _output_text(response)
                usage = getattr(response, "usage", None)
                telemetry = ProviderTelemetry(
                    provider="openai",
                    model=str(getattr(response, "model", self.config.model) or self.config.model),
                    request_id=str(getattr(response, "id", "") or "") or None,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                    output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
                    total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
                    response_status=str(getattr(response, "status", "completed") or "completed"),
                )
                return TradeDecision.model_validate_json(text), telemetry, text
            except Exception as exc:
                last_error = exc
                if attempt < self.config.max_retries:
                    await asyncio.sleep(0.5)
        return None, ProviderTelemetry(provider="openai", model=self.config.model, latency_ms=(time.perf_counter() - started) * 1000, response_status="error", error_category=last_error.__class__.__name__ if last_error else "unknown"), None

    def _client(self):
        from openai import OpenAI

        return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def _responses_create(self, context: dict[str, Any]):
        client = self._client()
        return client.responses.create(
            model=self.config.model,
            input=[
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": json.dumps(context, separators=(",", ":"), sort_keys=True)},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "ai_trade_decision",
                    "schema": decision_schema(),
                    "strict": True,
                }
            },
            max_output_tokens=self.config.max_output_tokens,
        )

    def _health_responses_create(self):
        client = self._client()
        return client.responses.create(
            model=self.config.model,
            input="Return OK.",
            max_output_tokens=16,
        )


def _system_prompt() -> str:
    return (
        "You are a trading decision evaluator for a tightly guarded paper-trading system. "
        "Return only the required structured decision. Do not claim data that is not present. "
        "Choose NO_TRADE when evidence is insufficient or conflicting. "
        "Allowed active decisions are LONG, SHORT, and NO_TRADE. "
        "The deterministic risk engine has final authority over eligibility, sizing, and broker submission. "
        "You have no permission to submit, modify, or cancel broker orders."
    )


def _output_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    if text:
        return str(text)
    parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            value = getattr(content, "text", None)
            if value:
                parts.append(str(value))
    if parts:
        return "".join(parts)
    raise ValueError("OpenAI response did not contain output_text")
