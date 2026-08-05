from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.ai_assistant.models import ExplanationDomain, Freshness, Quality


def domain_freshness(
    domain: ExplanationDomain,
    values: dict[str, Any],
    *,
    timestamp: datetime | None,
    now: datetime | None = None,
) -> Freshness:
    if values.get("stale") is True or values.get("superseded") is True:
        return Freshness.SUPERSEDED
    if values.get("status") in {"REVOKED", "EXPIRED", "invalid", "INVALID"}:
        return Freshness.SUPERSEDED
    if domain is ExplanationDomain.RESEARCH:
        return Freshness.IMMUTABLE if values else Freshness.INCOMPLETE
    if domain in {ExplanationDomain.ORDER, ExplanationDomain.RECONCILIATION}:
        return Freshness.IMMUTABLE if values else Freshness.INCOMPLETE
    if timestamp is None:
        return Freshness.UNKNOWN if values else Freshness.INCOMPLETE

    current = now or datetime.now(timezone.utc)
    age = max(0.0, (current - timestamp).total_seconds())
    thresholds = {
        ExplanationDomain.MARKET_STRUCTURE: 15 * 60,
        ExplanationDomain.STRATEGY: 24 * 60 * 60,
        ExplanationDomain.RISK: 15 * 60,
        ExplanationDomain.POSITION: 5 * 60,
    }
    return Freshness.STALE if age > thresholds.get(domain, 24 * 60 * 60) else Freshness.CURRENT


def domain_quality(domain: ExplanationDomain, values: dict[str, Any]) -> Quality:
    if not values:
        return Quality.INVALID
    if values.get("is_fallback") is True:
        return Quality.FALLBACK
    if values.get("is_simulated") is True or domain in {ExplanationDomain.ORDER, ExplanationDomain.POSITION}:
        return Quality.SIMULATED
    if values.get("is_delayed") is True:
        return Quality.DELAYED
    if any(value in (None, "", [], {}) for value in values.values()):
        return Quality.PARTIAL
    return Quality.VALID
