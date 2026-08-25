"""Deterministic trade-confidence scoring for the MT5 autonomous entry cycle.

Replaces the OpenAI-based entry decision (backend/brokers/mt5/autonomous.py's old
_ai_decision). No AI model is used anywhere in this module. Every component score
is derived from data already computed elsewhere in the existing pipeline
(_score_candidate's multi-timeframe trend alignment, _entry_quality_score's SMC/ICT
structure score, take_profit.select_take_profit's reward:risk, MT5TradeMemorySnapshotORM's
recorded win-rate/expectancy) -- nothing here reimplements indicator/structure math,
it only combines already-computed signals into one explainable 0-100 score.

Determinism: compute_trade_confidence() is a pure function of its inputs. The same
candidate/entry_quality/memory/portfolio state always produces the same score.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# --- Weights -----------------------------------------------------------------
# Nine components, default weights sum to 1.0. Each is env-overridable
# (MT5_CONF_WEIGHT_<NAME>) and the full set is re-normalized at read time so a
# partial override never silently breaks the 0-100 scale.
_DEFAULT_WEIGHTS: dict[str, float] = {
    "trend_multi_timeframe": 0.20,
    "structure_confluence": 0.18,
    "reward_risk_quality": 0.14,
    "volatility_suitability": 0.08,
    "execution_conditions": 0.06,
    "signal_freshness": 0.04,
    "strategy_performance": 0.10,
    "symbol_performance": 0.10,
    "correlation_quality": 0.10,
}

# Confidence bands (inclusive lower bound).
_BANDS: tuple[tuple[float, str], ...] = (
    (90.0, "exceptional"),
    (85.0, "very_strong"),
    (80.0, "strong"),
    (75.0, "valid_autonomous"),
    (70.0, "observe_only"),
    (0.0, "reject"),
)

# A "good" reward:risk multiple maps to a full reward_risk_quality score of 100;
# anything at/below the system's MIN_REWARD_MULTIPLE floor maps to 50 (still
# tradeable per take_profit.py's own gate, but not a standout).
_REWARD_RISK_FLOOR = 1.5
_REWARD_RISK_TARGET = 3.0

# Memory-derived performance components: recommendation-based caps prevent a
# lucky small sample from producing an inflated score, without needing a
# statistically calibrated model.
_RECOMMENDATION_CAPS = {"AVOID": 30.0, "REDUCE_RISK": 55.0}
_NEUTRAL_PERFORMANCE_SCORE = 60.0  # cold start: no evidence for OR against
_MIN_SAMPLE_FOR_TRUST = 10  # below this, blend toward neutral rather than trust the raw win_rate
# 2026-08-25 MTFAI1 V2 confidence-component forensic analysis (Part 2): _RECOMMENDATION_CAPS was
# being applied unconditionally -- the raw_score BLEND above already tapers a thin sample's raw
# win_rate toward neutral(60), but the recommendation-derived CAP had no such gate, so a single
# post-cutover observation (real case found: strategy_performance sample=1, win_rate=0.0, one
# still-OPEN position) could set recommendation=REDUCE_RISK and floor every subsequent candidate
# at the cap regardless of how thin the evidence was. Generic fix, affects every strategy that
# reads strategy_performance/symbol_performance identically -- no strategy-specific branch.
# Below this many CLOSED trades, the cap is skipped entirely and raw_score (already
# sample-blended toward neutral above) is used as-is; env-overridable, defaults equal to
# _MIN_SAMPLE_FOR_TRUST so the two sample-size gates stay in sync unless deliberately split.
_MIN_SAMPLE_FOR_RECOMMENDATION_CAP = int(os.getenv("MT5_PERFORMANCE_RECOMMENDATION_MIN_SAMPLE", str(_MIN_SAMPLE_FOR_TRUST)))


def _weights() -> dict[str, float]:
    raw = {name: _env_float(f"MT5_CONF_WEIGHT_{name.upper()}", default) for name, default in _DEFAULT_WEIGHTS.items()}
    total = sum(raw.values()) or 1.0
    return {name: value / total for name, value in raw.items()}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


@dataclass(frozen=True)
class ConfidenceComponent:
    name: str
    score: float          # 0-100
    weight: float          # normalized, sums to 1.0 across all components
    contribution: float     # score * weight
    reason: str
    inputs: dict[str, Any] = field(default_factory=dict)


def _trend_multi_timeframe(candidate: dict[str, Any]) -> ConfidenceComponent:
    # 2026-08-25 Confidence Architecture & Calibration Audit: candidate["ranking_score"] is NOT
    # a trend-alignment measurement for every strategy -- for mtfai1 it's 88-spread_penalty (a
    # spread-cost proxy, mislabeled here, and double-counted against volatility_suitability
    # below, which is also spread/ATR-derived). Any strategy can now supply its own genuinely
    # graduated 0-100 trend-quality read via context["trend_quality_score"] (see MTFAI1 V2's
    # _mtfai1_v2_trend_quality in autonomous.py -- ADX(14) + MA-separation/ATR + real swing-based
    # H1/H4 structural agreement, none of which touches spread). This stays strategy-agnostic by
    # design -- confidence.py never branches on a strategy_id -- falling back to the original
    # ranking_score-based read for every strategy that hasn't supplied one yet.
    context = candidate.get("context") or {}
    explicit = context.get("trend_quality_score")
    if explicit is not None:
        try:
            score = _clamp(float(explicit))
        except (TypeError, ValueError):
            explicit = None
        else:
            return ConfidenceComponent("trend_multi_timeframe", score, 0.0, 0.0, f"strategy-supplied trend-quality score {score:.1f}", {"trend_quality_score": score, "source": "strategy_specific", "breakdown": context.get("trend_quality_breakdown")})
    raw = float(candidate.get("ranking_score") or 0.0)
    score = _clamp(raw)
    return ConfidenceComponent("trend_multi_timeframe", score, 0.0, 0.0, f"M15/H1/H4 trend-alignment score {raw:.1f} (generic fallback)", {"ranking_score": raw, "source": "generic_fallback"})


def _structure_confluence(entry_quality: dict[str, Any]) -> ConfidenceComponent:
    total = entry_quality.get("total_score")
    if total is None:
        return ConfidenceComponent("structure_confluence", _NEUTRAL_PERFORMANCE_SCORE, 0.0, 0.0, "SMC/ICT structure score unavailable, neutral default", {"status": entry_quality.get("status")})
    score = _clamp(float(total) * 100.0)
    positive = entry_quality.get("positive_contributors") or []
    reason = f"SMC/ICT structure score {float(total):.2f}" + (f", confirmed: {', '.join(positive[:3])}" if positive else "")
    return ConfidenceComponent("structure_confluence", score, 0.0, 0.0, reason, {"total_score": total, "trend_state": entry_quality.get("trend_state")})


def _reward_risk_quality(candidate: dict[str, Any]) -> ConfidenceComponent:
    # 2026-08-25 MTFAI1 V2 confidence-component forensic analysis: this component's global
    # _REWARD_RISK_FLOOR/_REWARD_RISK_TARGET (1.5/3.0) are calibrated to V1's own
    # MIN_REWARD_MULTIPLE=1.5 -- confirmed against real DEMO data that MTFAI1 V2's own deliberate
    # FVG/OB-target floor (risk_reward~=1.0) always scored exactly 33.33/100 here, a real tax on
    # V2's intentional design, not a signal of a bad setup. Same strategy-agnostic-hook pattern as
    # trend_multi_timeframe's context["trend_quality_score"] above: a strategy may supply its own
    # geometry-aware reward:risk read via context["reward_risk_quality_score"] (see MTFAI1 V2's
    # own _mtfai1_v2_reward_risk_quality in autonomous.py -- structural-destination quality,
    # V2-appropriate floor/target, ATR-normalized distance, reachability past opposing structure,
    # explicitly NOT "1R=100"); this function never branches on strategy_id, and every strategy
    # that hasn't supplied one keeps using the exact formula below, completely unchanged.
    context = candidate.get("context") or {}
    explicit_quality = context.get("reward_risk_quality_score")
    if explicit_quality is not None:
        try:
            score = _clamp(float(explicit_quality))
        except (TypeError, ValueError):
            explicit_quality = None
        else:
            return ConfidenceComponent("reward_risk_quality", score, 0.0, 0.0, f"strategy-supplied reward:risk-quality score {score:.1f}", {"reward_risk_quality_score": score, "source": "strategy_specific", "breakdown": context.get("reward_risk_quality_breakdown")})
    raw_rr = context.get("risk_reward") or candidate.get("risk_reward")
    try:
        rr = float(raw_rr) if raw_rr is not None else None
    except (TypeError, ValueError):
        rr = None
    if rr is None or rr <= 0:
        return ConfidenceComponent("reward_risk_quality", 0.0, 0.0, 0.0, "reward:risk unavailable", {"risk_reward": raw_rr})
    if rr <= _REWARD_RISK_FLOOR:
        score = 50.0 * (rr / _REWARD_RISK_FLOOR)
    else:
        span = _REWARD_RISK_TARGET - _REWARD_RISK_FLOOR
        score = 50.0 + 50.0 * min(1.0, (rr - _REWARD_RISK_FLOOR) / span)
    return ConfidenceComponent("reward_risk_quality", _clamp(score), 0.0, 0.0, f"reward:risk {rr:.2f} (floor {_REWARD_RISK_FLOOR}, target {_REWARD_RISK_TARGET})", {"risk_reward": rr})


def _volatility_suitability(candidate: dict[str, Any]) -> ConfidenceComponent:
    context = candidate.get("context") or {}
    try:
        atr = float(context.get("atr") or 0)
        spread = float(context.get("spread") or 0)
    except (TypeError, ValueError):
        atr = spread = 0.0
    if atr <= 0:
        return ConfidenceComponent("volatility_suitability", _NEUTRAL_PERFORMANCE_SCORE, 0.0, 0.0, "ATR unavailable, neutral default", {"atr": atr, "spread": spread})
    spread_ratio_pct = (spread / atr) * 100.0
    score = _clamp(100.0 - spread_ratio_pct * 3.0)
    return ConfidenceComponent("volatility_suitability", score, 0.0, 0.0, f"spread is {spread_ratio_pct:.1f}% of ATR", {"atr": atr, "spread": spread})


def _execution_conditions(candidate: dict[str, Any], *, degraded_flags: list[str] | None = None) -> ConfidenceComponent:
    flags = degraded_flags or []
    score = _clamp(100.0 - 20.0 * len(flags))
    reason = "no execution-quality warnings" if not flags else f"degraded: {', '.join(flags)}"
    return ConfidenceComponent("execution_conditions", score, 0.0, 0.0, reason, {"degraded_flags": flags})


def _signal_freshness(candidate: dict[str, Any], *, now: datetime | None = None) -> ConfidenceComponent:
    context = candidate.get("context") or {}
    timestamp = context.get("timestamp")
    if not timestamp:
        return ConfidenceComponent("signal_freshness", _NEUTRAL_PERFORMANCE_SCORE, 0.0, 0.0, "candle timestamp unavailable, neutral default", {})
    try:
        candle_time = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if candle_time.tzinfo is None:
            candle_time = candle_time.replace(tzinfo=timezone.utc)
        reference = now or datetime.now(timezone.utc)
        age_minutes = max(0.0, (reference - candle_time).total_seconds() / 60.0)
    except Exception:
        return ConfidenceComponent("signal_freshness", _NEUTRAL_PERFORMANCE_SCORE, 0.0, 0.0, "candle timestamp unparseable, neutral default", {"timestamp": timestamp})
    # Full marks within one M5 candle (5 min); linear decay to 0 by 30 min stale.
    score = _clamp(100.0 - max(0.0, age_minutes - 5.0) * (100.0 / 25.0))
    return ConfidenceComponent("signal_freshness", score, 0.0, 0.0, f"signal is {age_minutes:.1f} minutes old", {"age_minutes": age_minutes})


def _performance_component(name: str, memory: dict[str, Any] | None) -> ConfidenceComponent:
    if not memory:
        return ConfidenceComponent(name, _NEUTRAL_PERFORMANCE_SCORE, 0.0, 0.0, "no recorded history, neutral default", {})
    sample = int(memory.get("closed_trade_count") or 0)
    win_rate = float(memory.get("win_rate") or 0.0)
    recommendation = str(memory.get("recommendation") or "INSUFFICIENT_DATA").upper()
    raw_score = _clamp(win_rate * 100.0)
    if sample < _MIN_SAMPLE_FOR_TRUST:
        # Blend toward the neutral prior in proportion to how thin the sample is.
        blend = sample / _MIN_SAMPLE_FOR_TRUST
        raw_score = raw_score * blend + _NEUTRAL_PERFORMANCE_SCORE * (1 - blend)
    # A recommendation of AVOID/REDUCE_RISK derived from too few CLOSED trades must not be
    # allowed to floor confidence -- the raw_score blend above already handles thin-sample
    # win_rate; this gate does the same for the cap, independently, since the two previously had
    # no shared minimum.
    cap = _RECOMMENDATION_CAPS.get(recommendation) if sample >= _MIN_SAMPLE_FOR_RECOMMENDATION_CAP else None
    score = min(raw_score, cap) if cap is not None else raw_score
    reason = f"{sample} closed trades, win_rate={win_rate:.2f}, recommendation={recommendation}"
    return ConfidenceComponent(name, _clamp(score), 0.0, 0.0, reason, {"sample": sample, "win_rate": win_rate, "recommendation": recommendation, "expectancy": memory.get("expectancy")})


def _correlation_quality(*, correlation_penalty_points: float, correlated_symbols: list[str] | None = None) -> ConfidenceComponent:
    score = _clamp(100.0 - correlation_penalty_points)
    reason = "no significant correlated exposure" if correlation_penalty_points <= 0 else f"correlated with {', '.join(correlated_symbols or [])}: -{correlation_penalty_points:.0f}pts"
    return ConfidenceComponent("correlation_quality", score, 0.0, 0.0, reason, {"correlation_penalty_points": correlation_penalty_points, "correlated_symbols": correlated_symbols or []})


def compute_trade_confidence(
    *,
    candidate: dict[str, Any],
    entry_quality: dict[str, Any],
    symbol_memory: dict[str, Any] | None,
    global_memory: dict[str, Any] | None,
    correlation_penalty_points: float = 0.0,
    correlated_symbols: list[str] | None = None,
    degraded_execution_flags: list[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Pure, deterministic. Returns a dict with overall_score (0-100), band,
    eligible flag (score-only, NOT portfolio-gated), and every component for
    persistence/explainability."""
    weights = _weights()
    components = [
        _trend_multi_timeframe(candidate),
        _structure_confluence(entry_quality),
        _reward_risk_quality(candidate),
        _volatility_suitability(candidate),
        _execution_conditions(candidate, degraded_flags=degraded_execution_flags),
        _signal_freshness(candidate, now=now),
        _performance_component("strategy_performance", global_memory),
        _performance_component("symbol_performance", symbol_memory),
        _correlation_quality(correlation_penalty_points=correlation_penalty_points, correlated_symbols=correlated_symbols),
    ]
    weighted: list[ConfidenceComponent] = []
    overall = 0.0
    for component in components:
        weight = weights[component.name]
        contribution = component.score * weight
        overall += contribution
        weighted.append(ConfidenceComponent(component.name, component.score, weight, contribution, component.reason, component.inputs))
    overall = _clamp(overall)
    return {
        "overall_score": round(overall, 2),
        "band": classify_confidence_band(overall),
        "components": [
            {"name": c.name, "score": round(c.score, 2), "weight": round(c.weight, 4), "contribution": round(c.contribution, 2), "reason": c.reason, "inputs": c.inputs}
            for c in weighted
        ],
        "rule_version": "deterministic_confidence_v1",
    }


def classify_confidence_band(score: float) -> str:
    for threshold, band in _BANDS:
        if score >= threshold:
            return band
    return "reject"


def is_autonomous_eligible(score: float, *, min_trade_confidence: float = 75.0) -> bool:
    return score >= min_trade_confidence


def rank_candidates(scored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic ranking of already-scored candidates. Risk-adjustment (reward:risk
    quality, volatility suitability, correlation quality) is already baked into
    overall_score by compute_trade_confidence, so ranking purely by overall_score IS
    the risk-adjusted ranking -- this function does not re-weight or second-guess it.
    Ties broken by reward:risk, then symbol name, for full reproducibility."""
    def _key(row: dict[str, Any]) -> tuple[float, float, str]:
        confidence = row.get("trade_confidence") or {}
        overall = float(confidence.get("overall_score") or 0.0)
        context = row.get("context") or {}
        try:
            rr = float(context.get("risk_reward") or 0.0)
        except (TypeError, ValueError):
            rr = 0.0
        symbol = str(row.get("canonical_pair") or "")
        return (overall, rr, symbol)

    ordered = sorted(scored, key=_key, reverse=True)
    for index, row in enumerate(ordered, start=1):
        row["rank"] = index
    return ordered
