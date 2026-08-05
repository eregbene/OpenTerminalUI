from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


MAX_REQUEST_BYTES = 16_384
MAX_RESPONSE_BYTES = 256_000
MAX_EVIDENCE_ITEMS = 12
MAX_VALUE_CHARS = 6_000
MAX_IDENTIFIER_LENGTH = 160
MAX_STRING_LENGTH = 2_000
MAX_ARRAY_LENGTH = 100
MAX_DEPTH = 6
MAX_AUDIT_BYTES = 32_000
MAX_BUNDLE_BYTES = 512_000
MAX_LINEAGE_NODES = 64
MAX_LINEAGE_EDGES = 96
MAX_LINEAGE_DEPTH = 6
MAX_LINEAGE_DURATION_MS = 250
BUNDLE_RETENTION_DAYS = 30
AUDIT_RETENTION_DAYS = 90
CLEANUP_BATCH_SIZE = 500
MAX_STORAGE_BYTES = 50_000_000

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
BUNDLE_ID_RE = re.compile(r"^bundle_[a-f0-9]{12}$")
SENSITIVE_KEY_RE = re.compile(
    r"(password|secret|token|api[_-]?key|authorization|cookie|connection[_-]?string|private[_-]?key)",
    re.IGNORECASE,
)

ALLOW_LISTS: dict[str, set[str]] = {
    "risk_evaluation": {
        "evaluation_id",
        "decision",
        "requested_quantity",
        "approved_quantity",
        "risk_policy_id",
        "risk_policy_version",
        "rules_evaluated",
        "blocking_reasons",
        "resizing_reasons",
        "warnings",
        "created_at",
        "expires_at",
        "account_id",
        "deployment_id",
    },
    "paper_order": {
        "order_id",
        "status",
        "instrument_id",
        "symbol",
        "side",
        "quantity",
        "filled_quantity",
        "average_fill_price",
        "execution_model_version",
        "created_at",
        "updated_at",
        "account_id",
        "deployment_id",
        "strategy_id",
        "strategy_version",
        "risk_evaluation_id",
        "version",
    },
    "paper_fill": {
        "fill_id",
        "order_id",
        "status",
        "instrument_id",
        "side",
        "quantity",
        "price",
        "created_at",
        "account_id",
        "deployment_id",
        "execution_model_version",
        "sequence",
        "fills",
    },
    "position": {
        "position_id",
        "instrument_id",
        "symbol",
        "quantity",
        "average_price",
        "market_value",
        "realized_pnl",
        "unrealized_pnl",
        "updated_at",
    },
    "account_snapshot": {
        "account",
        "positions",
        "exposure",
        "pnl",
        "risk_state",
        "deployment_state",
        "timestamp",
    },
    "reconciliation": {
        "reconciliation_id",
        "account_id",
        "status",
        "differences",
        "calculated_equity",
        "stored_equity",
        "created_at",
    },
    "deployment": {
        "deployment_id",
        "account_id",
        "candidate_id",
        "strategy_id",
        "strategy_version",
        "status",
        "risk_policy",
        "execution_model",
        "created_at",
        "updated_at",
        "version",
        "stale",
        "stale_reasons",
    },
    "research_run": {
        "run_id",
        "status",
        "strategy_id",
        "strategy_version",
        "lineage",
        "result",
        "metrics",
        "created_at",
        "completed_at",
        "owner",
    },
    "scorecard": {
        "scorecard_id",
        "strategy_id",
        "strategy_version",
        "total_score",
        "components",
        "gates",
        "passed",
        "lineage",
        "created_at",
        "owner",
    },
    "candidate": {
        "candidate_id",
        "strategy_id",
        "strategy_version",
        "optimization_job_id",
        "validation_job_id",
        "scorecard_id",
        "promotion_status",
        "created_at",
        "stale",
        "owner",
    },
    "strategy_decision": {"strategy_id", "strategy", "parameters", "rules", "status", "version"},
    "snapshot": {"snapshot_id", "symbol", "timeframe", "trend", "bias", "status", "created_at", "configuration_hash"},
}


class SafeAPIError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.correlation_id = f"err_{uuid4().hex[:12]}"
        super().__init__(message)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_identifier(value: str, *, kind: str = "identifier") -> str:
    raw = str(value or "").strip()
    lowered = raw.lower()
    if kind == "bundle_id":
        if not BUNDLE_ID_RE.match(raw):
            raise SafeAPIError(422, "INVALID_REFERENCE", "invalid reference")
        return raw
    if (
        not SAFE_ID_RE.match(raw)
        or ".." in raw
        or "/" in raw
        or "\\" in raw
        or "%2e" in lowered
        or "%2f" in lowered
        or "%5c" in lowered
        or any(ord(ch) < 32 for ch in raw)
    ):
        raise SafeAPIError(422, "INVALID_REFERENCE", "invalid reference")
    return raw


def sanitize_value(value: Any, *, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        return "[omitted:max-depth]"
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in list(value.items())[:MAX_ARRAY_LENGTH]:
            key_str = str(key)
            if SENSITIVE_KEY_RE.search(key_str):
                output[key_str] = "[redacted]"
            else:
                output[key_str] = sanitize_value(item, depth=depth + 1)
        if len(value) > MAX_ARRAY_LENGTH:
            output["truncated"] = True
            output["omitted_item_count"] = len(value) - MAX_ARRAY_LENGTH
        return output
    if isinstance(value, list):
        items = [sanitize_value(item, depth=depth + 1) for item in value[:MAX_ARRAY_LENGTH]]
        if len(value) > MAX_ARRAY_LENGTH:
            items.append({"truncated": True, "omitted_item_count": len(value) - MAX_ARRAY_LENGTH})
        return items
    if isinstance(value, str):
        if SENSITIVE_KEY_RE.search(value) and ("=" in value or ":" in value):
            return "[redacted]"
        return value if len(value) <= MAX_STRING_LENGTH else value[:MAX_STRING_LENGTH] + "[truncated]"
    return value


def filter_evidence_values(entity_type: str, values: dict[str, Any]) -> dict[str, Any]:
    allowed = ALLOW_LISTS.get(entity_type)
    if not allowed:
        return sanitize_value(values)
    return sanitize_value({key: values[key] for key in allowed if key in values})


def sanitize_warnings(warnings: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(str(sanitize_value(item))[:240] for item in warnings)


def bounded_json_bytes(payload: Any) -> int:
    import json

    return len(json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8"))


@dataclass
class TokenBucket:
    capacity: int
    window_seconds: float
    hits: list[float]


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}

    def check(self, key: str, *, capacity: int, window_seconds: float = 60.0) -> None:
        current = time.monotonic()
        bucket = self._buckets.setdefault(key, TokenBucket(capacity=capacity, window_seconds=window_seconds, hits=[]))
        bucket.hits = [hit for hit in bucket.hits if current - hit < window_seconds]
        if len(bucket.hits) >= capacity:
            raise SafeAPIError(429, "RATE_LIMITED", "rate limit exceeded")
        bucket.hits.append(current)


rate_limiter = InMemoryRateLimiter()
