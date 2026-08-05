from __future__ import annotations

from typing import Any

from backend.economic_intelligence.calendar_guard import GuardResult, empty_result as _empty
from backend.economic_intelligence.config import EconomicIntelligenceConfig

_RISK_TO_DECISION = {"critical": "BLOCK", "high": "MANAGE_EXISTING_ONLY", "medium": "REDUCE_SIZE", "low": "ALLOW"}


def evaluate(
    classification: dict[str, Any] | None,
    *,
    spread_ratio: float | None,
    volatility_state: str | None,
    portfolio_exposure: float | None,
    position_open: bool,
    config: EconomicIntelligenceConfig,
) -> GuardResult:
    """Deterministic guard for unscheduled news classifications.

    Never emits BLOCK/force-close purely from a single low-confidence classification;
    low-confidence downgrades to at most REDUCE_SIZE/DELAY.
    """
    if not classification:
        return _empty("ALLOW", [])
    risk_level = str(classification.get("risk_level") or "low").lower()
    confidence = float(classification.get("confidence") or 0.0)
    urgency = str(classification.get("urgency") or "").lower()
    decision = _RISK_TO_DECISION.get(risk_level, "ALLOW")
    reasons = [f"NEWS_RISK_{risk_level.upper()}"]

    if confidence < config.ff_openai_min_confidence:
        if decision in {"BLOCK", "MANAGE_EXISTING_ONLY"}:
            decision = "REDUCE_SIZE" if urgency == "high" else "DELAY" if urgency else "ALLOW"
        reasons.append("NEWS_LOW_CONFIDENCE_DOWNGRADED")

    if spread_ratio is not None and spread_ratio > 3.0 and decision == "ALLOW":
        decision = "DELAY"
        reasons.append("NEWS_SPREAD_EXPANDED")

    if position_open and decision == "BLOCK":
        # For an existing position, "BLOCK" from unscheduled news means don't add/adjust risk,
        # never an automatic force-close.
        decision = "MANAGE_EXISTING_ONLY"
        reasons.append("NEWS_BLOCK_DOWNGRADED_FOR_OPEN_POSITION")

    size_multiplier = 0.5 if decision == "REDUCE_SIZE" else 1.0
    return {"decision": decision, "reason_codes": reasons, "nearest_event": None, "minutes_to_event": None, "recheck_at": None, "size_multiplier": size_multiplier}
