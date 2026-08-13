"""Confidence Validation & Calibration layer -- Parts 4-9 & 11: read-only analytics over
MT5CandidateEvaluationORM. Every function here only ever reads; nothing in this module writes
to the trading engine's configuration, weights, or threshold. This is evidence-gathering only
(per the brief: "Do not guess whether 75 is good. Build the evidence that tells us.").
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

from backend.brokers.mt5.orm import MT5CandidateEvaluationORM
from backend.shared.db import SessionLocal

CONFIDENCE_BANDS = ("60-69", "70-74", "75-79", "80-84", "85-89", "90+")
COMPONENT_NAMES = (
    "trend_multi_timeframe", "structure_confluence", "reward_risk_quality", "strategy_performance",
    "symbol_performance", "correlation_quality", "volatility_suitability", "execution_conditions", "signal_freshness",
)
REJECTION_REASONS = (
    "BELOW_CONFIDENCE_THRESHOLD", "PORTFOLIO_RISK_LIMIT", "CORRELATION_LIMIT", "EXISTING_POSITION",
    "INVALID_RR", "STALE_SIGNAL", "ECONOMIC_RISK", "CONTEXT_RISK",
)
DEFAULT_THRESHOLDS = (70.0, 72.5, 75.0, 77.5, 80.0, 82.5, 85.0, 90.0)


def sample_label(n: int) -> str:
    """Reporting labels only (Part 11) -- not hard scientific guarantees."""
    if n < 20:
        return "insufficient"
    if n < 50:
        return "low_confidence"
    if n < 100:
        return "preliminary"
    return "increasingly_useful"


def _band_bucket(score: float) -> str | None:
    if score < 60:
        return None
    if score < 70:
        return "60-69"
    if score < 75:
        return "70-74"
    if score < 80:
        return "75-79"
    if score < 85:
        return "80-84"
    if score < 90:
        return "85-89"
    return "90+"


def _effective_r(row: dict[str, Any]) -> float | None:
    """The one R figure that represents "what actually would have happened / did happen" for
    a resolved row, regardless of whether it was executed or shadow-tracked."""
    if row.get("outcome_type") == "EXECUTED" and row.get("realized_r") is not None:
        return row["realized_r"]
    return row.get("hypothetical_r")


def _resolved(row: dict[str, Any]) -> bool:
    return row.get("outcome_status") in {"TP_HIT", "SL_HIT", "EXPIRED_NO_TOUCH", "CLOSED"} and _effective_r(row) is not None


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return None
    return round(cov / ((var_x ** 0.5) * (var_y ** 0.5)), 4)


def _all_rows() -> list[dict[str, Any]]:
    with SessionLocal() as db:
        rows = db.query(MT5CandidateEvaluationORM).all()
        return [{column.name: getattr(row, column.name) for column in MT5CandidateEvaluationORM.__table__.columns} for row in rows]


def _stats_for_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = [row for row in rows if _resolved(row)]
    r_values = [_effective_r(row) for row in resolved]
    executed_with_costs = [row for row in resolved if row.get("outcome_type") == "EXECUTED" and row.get("net_pnl") is not None]
    wins = [r for r in r_values if r > 0]
    losses = [r for r in r_values if r < 0]
    breakeven = [r for r in r_values if r == 0]
    gross_win = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    mfe_values = [row["mfe_r"] for row in resolved if row.get("mfe_r") is not None]
    mae_values = [row["mae_r"] for row in resolved if row.get("mae_r") is not None]
    holding = [row["holding_duration_seconds"] for row in resolved if row.get("holding_duration_seconds") is not None]
    tp_hits = sum(1 for row in resolved if row.get("tp_hit"))
    sl_hits = sum(1 for row in resolved if row.get("sl_hit"))
    total_volume_rows = [row for row in executed_with_costs if row.get("total_trading_cost") is not None]
    total_cost = sum(row.get("total_trading_cost") or 0.0 for row in total_volume_rows)
    n = len(resolved)
    return {
        "candidates": len(rows),
        "resolved": n,
        "sample_label": sample_label(n),
        "win_rate": round(len(wins) / n, 4) if n else None,
        "loss_rate": round(len(losses) / n, 4) if n else None,
        "break_even_rate": round(len(breakeven) / n, 4) if n else None,
        "avg_r": round(statistics.fmean(r_values), 4) if r_values else None,
        "median_r": round(statistics.median(r_values), 4) if r_values else None,
        "expectancy": round(statistics.fmean(r_values), 4) if r_values else None,
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else None,
        "net_expectancy_r": round(statistics.fmean(r_values), 4) if r_values else None,
        "executed_cost_trades": len(executed_with_costs),
        "total_gross_pnl": round(sum(row.get("gross_pnl") or 0.0 for row in executed_with_costs), 2),
        "total_commission": round(sum(row.get("commission") or 0.0 for row in executed_with_costs), 2),
        "total_swap": round(sum(row.get("swap") or 0.0 for row in executed_with_costs), 2),
        "total_fees": round(sum(row.get("fee") or 0.0 for row in executed_with_costs), 2),
        "total_trading_cost": round(total_cost, 2),
        "total_net_pnl": round(sum(row.get("net_pnl") or 0.0 for row in executed_with_costs), 2),
        "cost_per_executed_trade": round(total_cost / len(executed_with_costs), 2) if executed_with_costs else None,
        "net_profitable": (statistics.fmean(r_values) > 0) if r_values else None,
        "avg_mfe_r": round(statistics.fmean(mfe_values), 4) if mfe_values else None,
        "avg_mae_r": round(statistics.fmean(mae_values), 4) if mae_values else None,
        "avg_holding_seconds": round(statistics.fmean(holding), 1) if holding else None,
        "tp_hit_rate": round(tp_hits / n, 4) if n else None,
        "sl_hit_rate": round(sl_hits / n, 4) if n else None,
    }


def confidence_band_report() -> dict[str, Any]:
    """Part 4: stats by confidence band, separately for executed trades vs shadow candidates."""
    rows = _all_rows()
    executed = [row for row in rows if row.get("outcome_type") == "EXECUTED"]
    shadow = [row for row in rows if row.get("outcome_type") == "SHADOW"]

    def _by_band(subset: list[dict[str, Any]]) -> dict[str, Any]:
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in subset:
            band = _band_bucket(row.get("overall_confidence") or 0)
            if band:
                buckets[band].append(row)
        return {band: _stats_for_group(buckets.get(band, [])) for band in CONFIDENCE_BANDS}

    return {"executed": _by_band(executed), "shadow": _by_band(shadow), "combined": _by_band(rows)}


def calibration_reliability_report() -> dict[str, Any]:
    """Part 5: is confidence calibrated? Correlations + band monotonicity. Evidence only --
    never mutates weights or the threshold."""
    rows = [row for row in _all_rows() if _resolved(row)]
    confidences = [row["overall_confidence"] for row in rows]
    r_values = [_effective_r(row) for row in rows]
    mfe_values = [row["mfe_r"] for row in rows if row.get("mfe_r") is not None]
    mfe_confidences = [row["overall_confidence"] for row in rows if row.get("mfe_r") is not None]
    positive = [1.0 if r > 0 else 0.0 for r in r_values]

    band_report = confidence_band_report()["combined"]
    band_avg_r = [(band, band_report[band]["avg_r"]) for band in CONFIDENCE_BANDS if band_report[band]["avg_r"] is not None]
    monotonic = all(band_avg_r[i][1] <= band_avg_r[i + 1][1] for i in range(len(band_avg_r) - 1)) if len(band_avg_r) >= 2 else None

    return {
        "sample_size": len(rows),
        "sample_label": sample_label(len(rows)),
        "confidence_vs_realized_r_correlation": _pearson(confidences, r_values),
        "confidence_vs_mfe_correlation": _pearson(mfe_confidences, mfe_values),
        "confidence_vs_positive_r_correlation": _pearson(confidences, positive),
        "band_avg_r_sequence": band_avg_r,
        "band_monotonic_with_confidence": monotonic,
    }


def component_effectiveness_report() -> list[dict[str, Any]]:
    """Part 6: for each of the 9 components, correlation between that component's own score
    and the eventual outcome R -- using only rows with a resolved future outcome."""
    rows = [row for row in _all_rows() if _resolved(row)]
    results = []
    for name in COMPONENT_NAMES:
        pairs = []
        for row in rows:
            component = next((c for c in (row.get("components") or []) if c.get("name") == name), None)
            if component is not None:
                pairs.append((component.get("score"), _effective_r(row)))
        scores = [p[0] for p in pairs if p[0] is not None]
        r_values = [p[1] for p in pairs if p[0] is not None]
        results.append({
            "component": name,
            "sample_size": len(scores),
            "sample_label": sample_label(len(scores)),
            "score_vs_outcome_r_correlation": _pearson(scores, r_values),
            "avg_score": round(statistics.fmean(scores), 2) if scores else None,
        })
    return results


def ranking_quality_report() -> dict[str, Any]:
    """Part 7: for cycles with multiple fully-scored candidates, how often rank #1 was
    actually the best-performing candidate, avg R by rank, and rank-vs-R correlation."""
    rows = [row for row in _all_rows() if _resolved(row) and row.get("rank")]
    by_cycle: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_cycle[row["cycle_id"]].append(row)
    multi_candidate_cycles = {cycle_id: group for cycle_id, group in by_cycle.items() if len(group) > 1}

    rank1_was_best = 0
    evaluated_cycles = 0
    avg_r_by_rank: dict[int, list[float]] = defaultdict(list)
    lower_rank_outperformed = 0
    for group in multi_candidate_cycles.values():
        evaluated_cycles += 1
        best_row = max(group, key=lambda row: _effective_r(row))
        rank1_row = next((row for row in group if row["rank"] == 1), None)
        if rank1_row is not None and best_row["candidate_id"] == rank1_row["candidate_id"]:
            rank1_was_best += 1
        if rank1_row is not None:
            for row in group:
                if row["rank"] and row["rank"] > 1 and _effective_r(row) > _effective_r(rank1_row) + 0.5:
                    lower_rank_outperformed += 1
        for row in group:
            avg_r_by_rank[row["rank"]].append(_effective_r(row))

    ranks = [row["rank"] for row in rows]
    r_values = [_effective_r(row) for row in rows]
    return {
        "sample_size": len(rows),
        "sample_label": sample_label(len(rows)),
        "multi_candidate_cycles": evaluated_cycles,
        "rank1_was_best_performing_rate": round(rank1_was_best / evaluated_cycles, 4) if evaluated_cycles else None,
        "avg_r_by_rank": {rank: round(statistics.fmean(values), 4) for rank, values in sorted(avg_r_by_rank.items())},
        "rank_vs_r_correlation": _pearson([float(r) for r in ranks], r_values),
        "cycles_where_lower_rank_substantially_outperformed": lower_rank_outperformed,
    }


def rejection_analysis_report() -> list[dict[str, Any]]:
    """Part 8: hypothetical outcome of rejected candidates, grouped by rejection reason."""
    rows = _all_rows()
    by_reason: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for reason in (row.get("rejection_reasons") or []):
            by_reason[reason].append(row)
    return [{"rejection_reason": reason, **_stats_for_group(by_reason.get(reason, []))} for reason in REJECTION_REASONS]


def threshold_simulation_report(thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS) -> list[dict[str, Any]]:
    """Part 9: hypothetical performance at alternative confidence thresholds, using only
    already-resolved historical candidate rows. Analytics only -- does not read, write, or
    otherwise touch MT5Config.min_trade_confidence (the live production threshold)."""
    rows = [row for row in _all_rows() if _resolved(row)]
    total_candidates = len(_all_rows())
    results = []
    for threshold in thresholds:
        qualifying = [row for row in rows if (row.get("overall_confidence") or 0) >= threshold]
        stats = _stats_for_group(qualifying)
        results.append({
            "threshold": threshold,
            "qualifying_candidates": len(qualifying),
            "pct_of_opportunities_filtered_out": round(1 - (len(qualifying) / total_candidates), 4) if total_candidates else None,
            **{key: stats[key] for key in ("win_rate", "avg_r", "expectancy", "profit_factor", "sample_label")},
        })
    return results


def recent_candidate_outcomes(limit: int = 50) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        rows = db.query(MT5CandidateEvaluationORM).order_by(MT5CandidateEvaluationORM.created_at.desc()).limit(limit).all()
        return [{column.name: getattr(row, column.name) for column in MT5CandidateEvaluationORM.__table__.columns} for row in rows]
