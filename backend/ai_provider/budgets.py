from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import uuid4

from backend.ai_assistant.audit import audit_log
from backend.ai_assistant.security import SafeAPIError, validate_identifier
from backend.ai_provider.usage import usage_ledger


@dataclass(frozen=True)
class ProviderLimits:
    per_request_tokens: int = int(os.getenv("AI_MAX_REQUEST_TOKENS", "8000"))
    per_conversation_tokens: int = int(os.getenv("AI_MAX_CONVERSATION_TOKENS", "100000"))
    daily_tokens_per_user: int = int(os.getenv("AI_DAILY_TOKENS_PER_USER", "250000"))
    daily_cost_per_user: Decimal = Decimal(os.getenv("AI_DAILY_COST_PER_USER", "5"))
    monthly_cost_per_user: Decimal = Decimal(os.getenv("AI_MONTHLY_COST_PER_USER", "50"))
    autonomous_research_cost_per_job: Decimal = Decimal(os.getenv("AI_RESEARCH_COST_PER_JOB", "2"))
    autonomous_research_daily_cost: Decimal = Decimal(os.getenv("AI_RESEARCH_DAILY_COST", "10"))
    max_provider_retries: int = int(os.getenv("AI_MAX_PROVIDER_RETRIES", "2"))
    max_concurrent_provider_requests: int = int(os.getenv("AI_MAX_CONCURRENT_PROVIDER_REQUESTS", "4"))
    soft_budget_ratio: Decimal = Decimal(os.getenv("AI_SOFT_BUDGET_RATIO", "0.8"))


class BudgetManager:
    def __init__(self) -> None:
        self.limits = ProviderLimits()
        self._lock = threading.RLock()
        self._reservations: dict[str, dict[str, Any]] = {}

    def reserve(self, *, user_id: str, conversation_id: str | None, estimated_tokens: int, estimated_cost: str, correlation_id: str | None = None) -> dict[str, Any]:
        if estimated_tokens < 0 or Decimal(str(estimated_cost)) < 0:
            raise SafeAPIError(422, "INVALID_USAGE", "invalid usage")
        with self._lock:
            active = sum(1 for row in self._reservations.values() if row["status"] == "active")
            if active >= self.limits.max_concurrent_provider_requests:
                raise SafeAPIError(429, "BUDGET_CONCURRENCY_EXCEEDED", "provider concurrency limit reached")
            usage = usage_ledger.aggregate(user_id=user_id)["aggregates"]
            day = _group_value(usage, f"user:{user_id}", "tokens")
            if estimated_tokens > self.limits.per_request_tokens or day + estimated_tokens > self.limits.daily_tokens_per_user:
                self._audit(user_id, "rejected", "token_budget", correlation_id)
                raise SafeAPIError(429, "BUDGET_EXCEEDED", "provider budget exceeded")
            reservation = {
                "reservation_id": f"res_{uuid4().hex[:12]}",
                "user_id": validate_identifier(user_id),
                "conversation_id": validate_identifier(conversation_id) if conversation_id else None,
                "estimated_tokens": estimated_tokens,
                "estimated_cost": str(estimated_cost),
                "status": "active",
                "warnings": [],
            }
            if day + estimated_tokens >= int(self.limits.daily_tokens_per_user * float(self.limits.soft_budget_ratio)):
                reservation["warnings"].append("daily_token_soft_budget")
            self._reservations[reservation["reservation_id"]] = reservation
            self._audit(user_id, "reserved", reservation["reservation_id"], correlation_id)
            return reservation

    def reconcile(self, reservation_id: str | None, *, status: str, actual_tokens: int = 0, actual_cost: str = "0", correlation_id: str | None = None) -> None:
        if not reservation_id:
            return
        with self._lock:
            row = self._reservations.get(validate_identifier(reservation_id))
            if row:
                row["status"] = status
                row["actual_tokens"] = actual_tokens
                row["actual_cost"] = actual_cost
                self._audit(row["user_id"], status, reservation_id, correlation_id)

    def status(self, *, user_id: str | None = None) -> dict[str, Any]:
        return {
            "limits": {k: str(v) for k, v in self.limits.__dict__.items()},
            "active_reservations": [row for row in self._reservations.values() if row["status"] == "active" and (not user_id or row["user_id"] == user_id)],
        }

    def _audit(self, actor: str, result: str, target: str, correlation_id: str | None) -> None:
        audit_log.record(event="ai_provider.budget", actor=actor, correlation_id=correlation_id, result_status=result, payload={"target": target})


def _group_value(groups: dict[str, Any], key: str, field: str) -> int:
    return int((groups.get(key) or {}).get(field) or 0)


budget_manager = BudgetManager()
