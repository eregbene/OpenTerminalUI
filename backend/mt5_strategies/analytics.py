"""Strategy-performance analytics (Part 15) -- read-only aggregation over the same
MT5CandidateEvaluationORM the Confidence Validation & Calibration layer already populates (see
backend/brokers/mt5/candidate_evaluation.py -- every confidence-scored candidate, selected,
lower-ranked, or rejected, is already persisted there with strategy/strategy_family/
contributing_strategies/outcome fields). Nothing here writes back into strategy weights,
activation, or the confidence threshold.

Reuses confidence_calibration.py's row-shaping and R/outcome helpers so the definitions of
"resolved", "win", "expectancy" etc. stay identical across both reports rather than drifting.

Scope note: "signals" (every raw StrategySignal a strategy produced this cycle, before top-K
ranking truncates the pool) is NOT persisted historically -- only candidates that survived to
confidence-scoring are. The live per-cycle count is available at
MT5AutonomousTradingService._last_screen_counters (Part 19); a rolling historical signal count
is a possible follow-up storage decision, deliberately not added here.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

from backend.brokers.mt5.confidence_calibration import _all_rows, _effective_r, _resolved, sample_label
from backend.mt5_strategies.models import STRATEGY_FAMILIES


def _stats_for_strategy_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = [row for row in rows if _resolved(row)]
    r_values = [_effective_r(row) for row in resolved]
    wins = [r for r in r_values if r > 0]
    losses = [r for r in r_values if r < 0]
    breakeven = [r for r in r_values if r == 0]
    gross_win = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    mfe_values = [row["mfe_r"] for row in resolved if row.get("mfe_r") is not None]
    mae_values = [row["mae_r"] for row in resolved if row.get("mae_r") is not None]
    holding = [row["holding_duration_seconds"] for row in resolved if row.get("holding_duration_seconds") is not None]
    confidences = [row["overall_confidence"] for row in rows if row.get("overall_confidence") is not None]
    executed = [row for row in rows if row.get("outcome_type") == "EXECUTED"]
    eligible = [row for row in rows if row.get("eligible_for_execution")]
    selected = [row for row in rows if row.get("selected")]
    portfolio_rejected = [row for row in rows if "PORTFOLIO_RISK_LIMIT" in (row.get("rejection_reasons") or [])]
    economic_rejected = [row for row in rows if "ECONOMIC_RISK" in (row.get("rejection_reasons") or [])]
    n = len(resolved)
    candidates_total = len(rows)
    return {
        "candidates_evaluated": candidates_total,
        "eligible_candidates": len(eligible),
        "selected_candidates": len(selected),
        "actual_trades": len(executed),
        "resolved_trades": n,
        "sample_label": sample_label(n),
        "wins": len(wins),
        "losses": len(losses),
        "break_even": len(breakeven),
        "win_rate": round(len(wins) / n, 4) if n else None,
        "avg_r": round(statistics.fmean(r_values), 4) if r_values else None,
        "median_r": round(statistics.median(r_values), 4) if r_values else None,
        "expectancy": round(statistics.fmean(r_values), 4) if r_values else None,
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else None,
        "avg_mfe_r": round(statistics.fmean(mfe_values), 4) if mfe_values else None,
        "avg_mae_r": round(statistics.fmean(mae_values), 4) if mae_values else None,
        "avg_confidence": round(statistics.fmean(confidences), 2) if confidences else None,
        "avg_holding_seconds": round(statistics.fmean(holding), 1) if holding else None,
        "portfolio_rejection_rate": round(len(portfolio_rejected) / candidates_total, 4) if candidates_total else None,
        "economic_risk_rejection_rate": round(len(economic_rejected) / candidates_total, 4) if candidates_total else None,
    }


def strategy_performance_report() -> dict[str, Any]:
    """Part 15: one row per PERSISTED CANDIDATE (never per contributing strategy), grouped by
    the candidate's ANCHOR strategy_id -- this is what avoids double-counting a single fused,
    executed trade as several independent trades. `contributing_strategies_seen` lists every
    strategy that contributed to at least one candidate anchored here, for context only; it does
    not add extra trade credit."""
    by_strategy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _all_rows():
        by_strategy[row.get("strategy") or "unknown"].append(row)
    report: dict[str, Any] = {}
    for strategy_id, group in by_strategy.items():
        stats = _stats_for_strategy_group(group)
        stats["contributing_strategies_seen"] = sorted({sid for row in group for sid in (row.get("contributing_strategies") or [])})
        stats["multi_strategy_confirmation_rate"] = round(sum(1 for row in group if row.get("multi_strategy_confirmation")) / len(group), 4) if group else None
        report[strategy_id] = stats
    return report


def strategy_family_performance_report() -> dict[str, Any]:
    """Same grouping/one-row-per-candidate rule as strategy_performance_report, but bucketed by
    strategy_family (falls back to STRATEGY_FAMILIES metadata if a persisted row predates the
    strategy_family column)."""
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _all_rows():
        family = row.get("strategy_family") or STRATEGY_FAMILIES.get(row.get("strategy") or "", {}).get("family") or "unknown"
        by_family[family].append(row)
    return {family: _stats_for_strategy_group(group) for family, group in by_family.items()}


def contribution_participation_report() -> dict[str, Any]:
    """Cross-tab only, explicitly NOT a trade-count table (Part 15's "avoid double-counting"):
    how often each strategy appears among a fused candidate's contributing_strategies, whether
    or not it was the anchor. A strategy can show up here without ever anchoring a trade --
    this answers "which strategies tend to agree with winners" without claiming independent
    trade credit for each contributor. Use strategy_performance_report for actual trade counts."""
    participation: dict[str, dict[str, int]] = defaultdict(lambda: {"candidates_participated_in": 0, "resolved_wins_participated_in": 0, "resolved_losses_participated_in": 0})
    for row in _all_rows():
        contributors = row.get("contributing_strategies") or ([row["strategy"]] if row.get("strategy") else [])
        resolved = _resolved(row)
        r = _effective_r(row) if resolved else None
        for strategy_id in contributors:
            bucket = participation[strategy_id]
            bucket["candidates_participated_in"] += 1
            if resolved and r is not None:
                if r > 0:
                    bucket["resolved_wins_participated_in"] += 1
                elif r < 0:
                    bucket["resolved_losses_participated_in"] += 1
    return dict(participation)
