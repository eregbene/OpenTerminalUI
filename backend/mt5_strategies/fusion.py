"""Candidate fusion + conflict resolution (Parts 12/13).

Turns a flat list of per-symbol StrategySignal objects (possibly several strategies agreeing
or disagreeing on the same symbol) into ONE unified candidate pool: same-direction signals on
the same symbol are fused into a single candidate that preserves every contributing strategy
id; opposite-direction signals on the same symbol are resolved deterministically rather than
both being submitted as separate, self-cancelling candidates.

Output candidates are shaped as plain dicts compatible with the existing MT5SchedulerCandidate
dict already produced by backend/brokers/mt5/autonomous.py::_screen (canonical_pair,
broker_symbol, direction, ranking_score, context, stop_loss, take_profit, rejection_reasons,
context_hash, ...), with new fields layered on top -- so the rest of run_cycle
(_rank_candidates_by_confidence, compute_trade_confidence, portfolio/economic gates, _submit)
needs no structural change to accept them.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from backend.mt5_strategies.models import ACTIVE_MT5, SHADOW_MT5, activation_status
from backend.mt5_strategies.models import StrategySignal

# Minimum strength gap for one side of a conflict to unambiguously dominate the other,
# without needing HTF direction as a tie-break.
_DOMINANCE_MARGIN = 15.0
_CONFIRMATION_BONUS_PER_EXTRA_STRATEGY = 4.0
_CONFIRMATION_BONUS_CAP = 16.0


def _hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _fuse_same_direction(symbol: str, direction: str, signals: list[StrategySignal]) -> dict[str, Any]:
    """Multiple strategies agreeing on the same symbol+direction become ONE candidate (Part
    12's EURUSD LONG example) -- never separate, duplicate orders for the same opportunity."""
    anchor = max(signals, key=lambda s: s.raw_signal_strength)
    extra_confirmations = len(signals) - 1
    fused_strength = min(100.0, anchor.raw_signal_strength + min(_CONFIRMATION_BONUS_CAP, extra_confirmations * _CONFIRMATION_BONUS_PER_EXTRA_STRATEGY))
    contributing = sorted({s.strategy_id for s in signals})
    return {
        "anchor": anchor,
        "fused_strength": fused_strength,
        "contributing_strategy_ids": contributing,
        "contributing_families": sorted({s.strategy_family for s in signals}),
        "multi_strategy_confirmation": extra_confirmations > 0,
    }


def _resolve_conflict(symbol: str, long_fused: dict[str, Any] | None, short_fused: dict[str, Any] | None, htf_trend_h4: str | None) -> tuple[dict[str, Any] | None, str]:
    """Part 13: deterministic conflict resolution. Never arbitrarily picks a side -- either one
    side clearly dominates (strength margin or HTF alignment), or both are rejected."""
    if long_fused is None:
        return short_fused, "NONE"
    if short_fused is None:
        return long_fused, "NONE"
    margin = long_fused["fused_strength"] - short_fused["fused_strength"]
    if abs(margin) >= _DOMINANCE_MARGIN:
        winner, state = (long_fused, "RESOLVED_DOMINANT_STRENGTH") if margin > 0 else (short_fused, "RESOLVED_DOMINANT_STRENGTH")
        return winner, state
    if htf_trend_h4 == "bullish":
        return long_fused, "RESOLVED_HTF_DIRECTION"
    if htf_trend_h4 == "bearish":
        return short_fused, "RESOLVED_HTF_DIRECTION"
    return None, "AMBIGUOUS_CONFLICT_REJECTED"


def build_candidates(
    *,
    symbol: str,
    broker_symbol: str,
    asset_class: str | None,
    cycle_id: str,
    signals: list[StrategySignal],
    htf_trend_h4: str | None,
    now: datetime,
) -> list[dict[str, Any]]:
    """Returns 0 or 1 candidate dict for this symbol (the fused/conflict-resolved
    opportunity), in the same shape _screen() already produces. Per-strategy evidence for the
    anchor is already carried, JSON-safe, on context["strategy_evidence"]; raw StrategySignal
    objects are never attached to the candidate dict (they aren't JSON-serializable, and
    persist_cycle_result/capture_cycle_candidate_evaluations both write the candidate straight
    to a JSON column)."""
    valid = [s for s in signals if s.valid]
    long_signals = [s for s in valid if s.direction == "LONG"]
    short_signals = [s for s in valid if s.direction == "SHORT"]
    long_fused = _fuse_same_direction(symbol, "LONG", long_signals) if long_signals else None
    short_fused = _fuse_same_direction(symbol, "SHORT", short_signals) if short_signals else None

    conflict_state = "NONE"
    winner = None
    if long_fused and short_fused:
        winner, conflict_state = _resolve_conflict(symbol, long_fused, short_fused, htf_trend_h4)
    elif long_fused or short_fused:
        winner = long_fused or short_fused

    if winner is None:
        return []

    anchor: StrategySignal = winner["anchor"]
    context = {
        "symbol": symbol,
        "broker_symbol": broker_symbol,
        "timestamp": anchor.generated_at.isoformat(),
        "direction": anchor.direction,
        "score": winner["fused_strength"],
        "risk_reward": str(anchor.reward_risk) if anchor.reward_risk is not None else None,
        "atr": anchor.metadata.get("atr") if anchor.metadata else None,
        "spread": anchor.metadata.get("spread") if anchor.metadata else None,
        "regime": anchor.regime,
        "strategy_id": anchor.strategy_id,
        "strategy_family": anchor.strategy_family,
        "contributing_strategies": winner["contributing_strategy_ids"],
        "contributing_families": winner["contributing_families"],
        "multi_strategy_confirmation": winner["multi_strategy_confirmation"],
        "conflict_state": conflict_state,
        "htf_trend_h4": htf_trend_h4,
        # Part 11/19: strategy_evidence carries the per-strategy evidence PLUS a nested
        # stop_geometry audit trail (structural reference, ATR, spread, broker minimum
        # distance, final stop distance, ATR-multiple bounds) -- folded into this existing
        # persisted JSON field rather than adding a new column, so "why did this trade use this
        # stop" is answerable from already-persisted calibration data without a migration.
        "strategy_evidence": {**anchor.evidence, "stop_geometry": anchor.metadata or {}},
        "timeframe_context": {"policy": "MT5_MULTI_STRATEGY", "timeframes": sorted({s.timeframe for s in signals})},
    }
    activation = activation_status(anchor.strategy_id)
    candidate = {
        "canonical_pair": symbol,
        "broker_symbol": broker_symbol,
        "asset_class": asset_class,
        "direction": anchor.direction,
        "ranking_score": winner["fused_strength"],
        "entry": anchor.proposed_entry,
        "stop_loss": anchor.stop_loss,
        "take_profit": anchor.take_profit,
        "risk_reward": anchor.reward_risk,
        "context": context,
        "context_hash": _hash({"cycle": cycle_id, "symbol": symbol, "strategy": anchor.strategy_id, "direction": anchor.direction}),
        "rejection_reasons": [] if conflict_state in {"NONE", "RESOLVED_DOMINANT_STRENGTH", "RESOLVED_HTF_DIRECTION"} else ["STRATEGY_CONFLICT_UNRESOLVED"],
        "strategy_activation": activation,
    }
    return [candidate]
