from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.intelligence.trading.config import AITradingConfig


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class AIUsageLedger:
    request_timestamps: list[datetime] = field(default_factory=list)
    context_hashes: set[str] = field(default_factory=set)
    input_tokens_today: int = 0
    output_tokens_today: int = 0
    estimated_cost_today: float = 0.0
    estimated_cost_month: float = 0.0
    last_request_timestamp: datetime | None = None
    symbols_skipped_without_model_call: int = 0
    duplicate_contexts_skipped: int = 0
    provider_calls_made: int = 0

    def requests_this_hour(self) -> int:
        cutoff = utcnow() - timedelta(hours=1)
        self.request_timestamps = [row for row in self.request_timestamps if row >= cutoff]
        return len(self.request_timestamps)

    def requests_today(self) -> int:
        today = utcnow().date()
        return len([row for row in self.request_timestamps if row.date() == today])

    def can_request(self, config: AITradingConfig, context_hash: str | None = None) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if context_hash and context_hash in self.context_hashes:
            self.duplicate_contexts_skipped += 1
            reasons.append("DUPLICATE_CONTEXT")
        if self.requests_this_hour() >= config.max_provider_requests_per_hour:
            reasons.append("HOURLY_PROVIDER_LIMIT")
        if self.requests_today() >= config.max_provider_requests_per_day:
            reasons.append("DAILY_PROVIDER_LIMIT")
        if self.estimated_cost_today >= config.daily_cost_limit_usd:
            reasons.append("DAILY_COST_LIMIT")
        if self.estimated_cost_month >= config.monthly_cost_limit_usd:
            reasons.append("MONTHLY_COST_LIMIT")
        return not reasons, reasons

    def record_request(self, *, context_hash: str | None, input_tokens: int, output_tokens: int, estimated_cost_usd: float) -> None:
        now = utcnow()
        self.request_timestamps.append(now)
        self.last_request_timestamp = now
        self.provider_calls_made += 1
        if context_hash:
            self.context_hashes.add(context_hash)
        self.input_tokens_today += int(input_tokens or 0)
        self.output_tokens_today += int(output_tokens or 0)
        self.estimated_cost_today += float(estimated_cost_usd or 0)
        self.estimated_cost_month += float(estimated_cost_usd or 0)

    def record_skip(self, count: int = 1) -> None:
        self.symbols_skipped_without_model_call += count

    def status(self, config: AITradingConfig, scheduler_status: dict[str, Any] | None = None) -> dict[str, Any]:
        next_time = None
        if self.last_request_timestamp:
            next_time = (self.last_request_timestamp + timedelta(seconds=config.analysis_interval_seconds)).isoformat()
        return {
            "model": config.model,
            "requests_this_hour": self.requests_this_hour(),
            "hourly_request_limit": config.max_provider_requests_per_hour,
            "requests_today": self.requests_today(),
            "daily_request_limit": config.max_provider_requests_per_day,
            "input_tokens_today": self.input_tokens_today,
            "output_tokens_today": self.output_tokens_today,
            "estimated_cost_today": round(self.estimated_cost_today, 6),
            "estimated_cost_this_month": round(self.estimated_cost_month, 6),
            "last_request_timestamp": self.last_request_timestamp.isoformat() if self.last_request_timestamp else None,
            "next_eligible_analysis_timestamp": next_time,
            "symbols_skipped_without_model_call": self.symbols_skipped_without_model_call,
            "duplicate_contexts_skipped": self.duplicate_contexts_skipped,
            "scheduler_status": scheduler_status or {},
        }


def estimate_openai_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    # Conservative configurable ledger estimate for gpt-4.1-mini family.
    input_rate_per_million = 0.40
    output_rate_per_million = 1.60
    return (input_tokens / 1_000_000 * input_rate_per_million) + (output_tokens / 1_000_000 * output_rate_per_million)


usage_ledger = AIUsageLedger()
