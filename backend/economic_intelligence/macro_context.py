from __future__ import annotations

import json
import logging
import os
from typing import Any

from backend.ai_provider import ProviderRequest, provider_registry
from backend.economic_intelligence.calendar_guard import GuardResult, combine
from backend.economic_intelligence.config import EconomicIntelligenceConfig

logger = logging.getLogger(__name__)

_VALID_ALIGNMENT = {"supportive", "opposing", "neutral", "uncertain"}
_VALID_RISK = {"low", "medium", "high", "critical"}
_VALID_ACTION = {"allow", "delay", "block", "reduce_size", "manage_existing_only"}


def build_context(
    *,
    candidate: dict[str, Any],
    calendar_result: GuardResult,
    news_result: GuardResult,
    nearby_events: list[dict[str, Any]],
    nearby_news: list[dict[str, Any]],
    spread: float | None,
    volatility: dict[str, Any] | None,
    exposure: dict[str, Any] | None,
    open_positions: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    return {
        "proposed_trade": {
            "symbol": candidate.get("canonical_pair") or candidate.get("symbol"),
            "direction": candidate.get("direction"),
            "strategy": candidate.get("strategy_id") or candidate.get("strategy"),
            "strategy_confidence": candidate.get("score") or candidate.get("ranking_score"),
        },
        "upcoming_related_events": nearby_events[:10],
        "recently_released_events": [e for e in nearby_events if e.get("status") == "released"][:10],
        "recent_related_news": nearby_news[:10],
        "current_spread": spread,
        "recent_volatility": volatility or {},
        "current_portfolio_exposure": exposure or {},
        "existing_open_positions": (open_positions or [])[:20],
        "calendar_guard_result": calendar_result,
        "news_guard_result": news_result,
    }


def _validate(data: dict[str, Any], config: EconomicIntelligenceConfig) -> dict[str, Any] | None:
    try:
        alignment = str(data.get("macro_alignment") or "").lower()
        risk_level = str(data.get("risk_level") or "").lower()
        action = str(data.get("recommended_action") or "").lower()
        raw_confidence = data.get("confidence")
        confidence = float(raw_confidence) if raw_confidence is not None else -1.0
    except Exception:
        return None
    if alignment not in _VALID_ALIGNMENT or risk_level not in _VALID_RISK or action not in _VALID_ACTION:
        return None
    if not (0.0 <= confidence <= 1.0):
        return None
    if confidence < config.ff_openai_min_confidence:
        return None
    return {
        "macro_alignment": alignment,
        "risk_level": risk_level,
        "affected_currencies": data.get("affected_currencies") or [],
        "directional_bias": data.get("directional_bias") or {},
        "confidence": confidence,
        "recommended_action": action,
        "recommended_constraints": data.get("recommended_constraints") or [],
        "reasoning_summary": str(data.get("reasoning_summary") or "")[:1000],
    }


async def classify(context: dict[str, Any], *, idempotency_key: str, config: EconomicIntelligenceConfig) -> dict[str, Any] | None:
    """OpenAI advisory macro classification. Returns None (never raises) on malformed output,
    timeout, provider quota, unavailable OpenAI, or low confidence -- caller falls back to
    calendar_guard + news_guard alone."""
    if not config.ff_openai_macro_classification_enabled:
        return None
    prompt = (
        "You are an advisory macro-context classifier for a demo MT5 forex trade. You do not place orders. "
        "Return strict JSON only, matching this schema: "
        '{"macro_alignment":"supportive|opposing|neutral|uncertain","risk_level":"low|medium|high|critical",'
        '"affected_currencies":["USD"],"directional_bias":{"USD":"bullish"},"confidence":0.0,'
        '"recommended_action":"allow|delay|block|reduce_size|manage_existing_only","recommended_constraints":[],'
        '"reasoning_summary":""}. Do not include full article text, only use the provided headlines/metadata. '
        + json.dumps(context, sort_keys=True, default=str)
    )
    try:
        response = await provider_registry.get("openai").complete(
            ProviderRequest(prompt=prompt, model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"), max_tokens=400, temperature=0, timeout_seconds=20, idempotency_key=idempotency_key)
        )
    except Exception as exc:
        logger.warning("Macro classification provider call failed: %s", exc.__class__.__name__)
        return None
    try:
        payload = json.loads(_strip_fences(response.text))
    except Exception:
        logger.warning("Macro classification returned malformed JSON")
        return None
    validated = _validate(payload, config)
    if validated is None:
        return None
    validated["model_provider"] = "openai"
    validated["model_name"] = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    validated["raw"] = payload
    return validated


def advisory_as_guard_result(classification: dict[str, Any] | None) -> GuardResult:
    if not classification:
        return {"decision": "ALLOW", "reason_codes": [], "nearest_event": None, "minutes_to_event": None, "recheck_at": None, "size_multiplier": 1.0}
    action = str(classification.get("recommended_action") or "allow").upper()
    decision = action if action in {"BLOCK", "DELAY", "REDUCE_SIZE", "MANAGE_EXISTING_ONLY", "ALLOW"} else "ALLOW"
    return {
        "decision": decision,
        "reason_codes": [f"OPENAI_MACRO_{decision}"],
        "nearest_event": None,
        "minutes_to_event": None,
        "recheck_at": None,
        "size_multiplier": 0.5 if decision == "REDUCE_SIZE" else 1.0,
    }


def combine_with_advisory(*results: GuardResult, advisory: dict[str, Any] | None) -> GuardResult:
    """OpenAI's advisory participates in the same precedence reducer as every deterministic
    guard -- it can never move the combined result toward ALLOW (test: OpenAI cannot override BLOCK)."""
    return combine(*results, advisory_as_guard_result(advisory))


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped
