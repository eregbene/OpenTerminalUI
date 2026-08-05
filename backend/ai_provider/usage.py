from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.ai_assistant.repository import AIAssistantRepository
from backend.ai_assistant.security import sanitize_value, validate_identifier
from backend.ai_provider.base import ProviderResponse


class UsageLedger:
    def __init__(self, root: Path | str = "data/ai_assistant") -> None:
        self.repo = AIAssistantRepository(root)
        self.path = self.repo.root / "provider_usage.json"

    def record(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        research_job_id: str | None,
        provider_response: ProviderResponse,
        reservation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        if min(provider_response.prompt_tokens, provider_response.completion_tokens, provider_response.cached_tokens, provider_response.reasoning_tokens) < 0:
            raise ValueError("provider usage cannot be negative")
        record = {
            "usage_id": f"usage_{uuid4().hex[:12]}",
            "timestamp": now.isoformat(),
            "day": now.date().isoformat(),
            "month": now.strftime("%Y-%m"),
            "user_id": validate_identifier(user_id),
            "conversation_id": validate_identifier(conversation_id) if conversation_id else None,
            "research_job_id": validate_identifier(research_job_id) if research_job_id else None,
            "provider": provider_response.provider,
            "model": provider_response.model,
            "input_tokens": provider_response.prompt_tokens,
            "output_tokens": provider_response.completion_tokens,
            "cached_input_tokens": provider_response.cached_tokens,
            "reasoning_tokens": provider_response.reasoning_tokens,
            "total_tokens": provider_response.total_tokens or provider_response.prompt_tokens + provider_response.completion_tokens + provider_response.reasoning_tokens,
            "provider_request_count": provider_response.provider_request_count,
            "retry_count": provider_response.retry_count,
            "latency_ms": provider_response.latency_ms,
            "time_to_first_token_ms": provider_response.time_to_first_token_ms,
            "stream_duration_ms": provider_response.stream_duration_ms,
            "cancelled": provider_response.cancelled,
            "usage_reported": provider_response.usage_reported,
            "estimated_cost": provider_response.estimated_cost,
            "confirmed_cost": provider_response.confirmed_cost,
            "currency": provider_response.currency,
            "pricing_version": provider_response.pricing_version,
            "reservation_id": reservation_id,
            "correlation_id": correlation_id,
        }
        raw = json.dumps(record, sort_keys=True, default=str, separators=(",", ":"))
        record["content_hash"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        data = self.repo._read(self.path, [])
        data.append(_sanitize_usage_record(record))
        self.repo._write(self.path, data)
        return record

    def list(self, *, user_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.repo._read(self.path, [])
        if user_id:
            return [row for row in rows if row.get("user_id") == user_id]
        return rows

    def aggregate(self, *, user_id: str | None = None) -> dict[str, Any]:
        rows = self.list(user_id=user_id)
        groups: dict[str, dict[str, Any]] = defaultdict(lambda: {"tokens": 0, "cost": 0.0, "requests": 0})
        for row in rows:
            for key in (
                f"user:{row.get('user_id')}",
                f"conversation:{row.get('conversation_id')}",
                f"provider:{row.get('provider')}",
                f"model:{row.get('model')}",
                f"day:{row.get('day')}",
                f"month:{row.get('month')}",
            ):
                groups[key]["tokens"] += int(row.get("total_tokens") or 0)
                groups[key]["cost"] += float(row.get("confirmed_cost") or row.get("estimated_cost") or 0)
                groups[key]["requests"] += 1
        return {"items": rows, "aggregates": groups}


usage_ledger = UsageLedger()


_NUMERIC_USAGE_FIELDS = {
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "reasoning_tokens",
    "total_tokens",
    "provider_request_count",
    "retry_count",
    "latency_ms",
    "time_to_first_token_ms",
    "stream_duration_ms",
}


def _sanitize_usage_record(record: dict[str, Any]) -> dict[str, Any]:
    sanitized = sanitize_value(record)
    for field in _NUMERIC_USAGE_FIELDS:
        sanitized[field] = record.get(field)
    return sanitized
