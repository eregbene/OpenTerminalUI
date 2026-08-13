"""Edge-discovery investigation Phase 1: failure forensics.

Answers "what was present before winning trades that was absent before immediate failures" by
MEASURING real feature-presence rates per outcome category, never assuming SMC/momentum/regime
matter a priori. Reuses columns outcomes.py::label_outcome() already computes and persists on
every trusted outcome (reached_0_25r ... reached_2r, mfe_r, mae_r, time_to_mfe_seconds,
time_to_0_5r_seconds, time_to_1r_seconds, immediate_failure) -- this module does not recompute
any of that; it only aggregates and cross-tabulates against the fingerprint's own real, already-
captured entry-time features (SMC booleans, regime, session, confidence_band, atr_regime,
spread_regime, direction).
"""
from __future__ import annotations

from typing import Any

_MILESTONES = ("reached_0_25r", "reached_0_5r", "reached_0_75r", "reached_1r", "reached_1_5r", "reached_2r")

WINNER = "WINNER"
LOSER = "LOSER"
IMMEDIATE_FAILURE = "IMMEDIATE_FAILURE"
LARGE_WINNER = "LARGE_WINNER"

_MIN_SAMPLE = 10


def _trusted_rows(*, anchor_strategy: str | None = None):
    from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM
    from backend.shared.db import SessionLocal

    with SessionLocal() as db:
        query = (
            db.query(HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM)
            .join(HistoricalSetupOutcomeORM, HistoricalSetupOutcomeORM.fingerprint_id == HistoricalPatternFingerprintORM.fingerprint_id)
            .filter(HistoricalSetupOutcomeORM.resolution_status == "RESOLVED", HistoricalSetupOutcomeORM.data_quality != "UNTRUSTED")
        )
        if anchor_strategy:
            query = query.filter(HistoricalPatternFingerprintORM.anchor_strategy == anchor_strategy)
        return query.all()


def _avg(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None]
    return round(sum(clean) / len(clean), 4) if clean else None


def milestone_reach_rates(group_by: str, *, min_sample: int = _MIN_SAMPLE) -> dict[str, Any]:
    """Real % reaching each R milestone, per real value of `group_by` (anchor_strategy /
    canonical_symbol / direction / session / regime / confidence_band / atr_regime /
    spread_regime -- any HistoricalPatternFingerprintORM column). Never fabricates a rate from
    below min_sample -- reported as INSUFFICIENT_SAMPLE instead."""
    from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM

    if not hasattr(HistoricalPatternFingerprintORM, group_by):
        raise ValueError(f"unknown group_by column: {group_by}")

    rows = _trusted_rows()
    grouped: dict[str, list[Any]] = {}
    for fp, outcome in rows:
        value = getattr(fp, group_by, None)
        if value is None:
            continue
        grouped.setdefault(str(value), []).append(outcome)

    results = {}
    for value, outcomes in grouped.items():
        n = len(outcomes)
        if n < min_sample:
            results[value] = {"n": n, "status": "INSUFFICIENT_SAMPLE"}
            continue
        reach_rates = {milestone: round(sum(1 for o in outcomes if getattr(o, milestone)) / n, 4) for milestone in _MILESTONES}
        results[value] = {
            "n": n,
            "status": "OK",
            **reach_rates,
            "never_reached_0_25r_pct": round(1 - reach_rates["reached_0_25r"], 4),
            "avg_mfe_r": _avg([o.mfe_r for o in outcomes]),
            "avg_mae_r": _avg([o.mae_r for o in outcomes]),
            "avg_time_to_mfe_seconds": _avg([o.time_to_mfe_seconds for o in outcomes]),
            "avg_time_to_0_5r_seconds": _avg([o.time_to_0_5r_seconds for o in outcomes]),
            "avg_time_to_1r_seconds": _avg([o.time_to_1r_seconds for o in outcomes]),
            "avg_outcome_r": _avg([o.outcome_r for o in outcomes]),
            "immediate_failure_rate": round(sum(1 for o in outcomes if o.immediate_failure) / n, 4),
        }
    return {"group_by": group_by, "groups": results}


def _categorize(outcome: Any) -> str:
    if outcome.immediate_failure:
        return IMMEDIATE_FAILURE
    if outcome.reached_2r:
        return LARGE_WINNER
    if (outcome.outcome_r or 0) > 0:
        return WINNER
    return LOSER


_FUNNEL_STAGES = ("all", "reached_0_25r", "reached_0_5r", "reached_0_75r", "reached_1r", "reached_1_5r", "reached_2r")

# MFE-based buckets (Phase 3 of the profit-capture forensic study): classifies each trade by
# how far it got, REGARDLESS of what it eventually realized -- deliberately independent of
# outcome_r so bucket membership isolates "did a real move happen" from "was it captured."
IMMEDIATE_FAILURE_BUCKET = "IMMEDIATE_FAILURE"  # never reached +0.25R
WEAK_MOVE = "WEAK_MOVE"  # reached +0.25R but not +0.5R
DEVELOPING_WINNER = "DEVELOPING_WINNER"  # reached +0.5R but not +1R
STRONG_WINNER = "STRONG_WINNER"  # reached +1R but not +2R
LARGE_WINNER_BUCKET = "LARGE_WINNER"  # reached +2R


def profit_leak_funnel(anchor_strategy: str) -> dict[str, Any]:
    """Phase 2 (profit-capture forensic study): for mtfai1/session_breakout, how much of the
    real, measured favorable price movement (MFE, via the already-persisted reached_*r booleans)
    survives to the STATIC, no-management exit (outcome_r, from outcomes.py::label_outcome() --
    original SL/TP only, confirmed no adaptive-management simulation). This isolates whether real
    entries have merit from whether ANY exit strategy captures that merit."""
    rows = _trusted_rows(anchor_strategy=anchor_strategy)
    outcomes = [o for _fp, o in rows]
    total_n = len(outcomes)

    stages = []
    for stage in _FUNNEL_STAGES:
        subset = outcomes if stage == "all" else [o for o in outcomes if getattr(o, stage)]
        n = len(subset)
        realized = [o.outcome_r for o in subset if o.outcome_r is not None]
        mfe = [o.mfe_r for o in subset if o.mfe_r is not None]
        captures = [o.outcome_r / o.mfe_r for o in subset if o.outcome_r is not None and o.mfe_r and o.mfe_r > 0]
        stages.append({
            "stage": stage,
            "n": n,
            "pct_of_all": round(n / total_n, 4) if total_n else None,
            "avg_eventual_mfe_r": _avg(mfe),
            "avg_realized_r": _avg(realized),
            "median_realized_r": (sorted(realized)[len(realized) // 2] if realized else None),
            "avg_capture_ratio": _avg(captures),
            "pct_eventually_losing": round(sum(1 for r in realized if r < 0) / len(realized), 4) if realized else None,
            "pct_eventually_be_or_small_win": round(sum(1 for r in realized if 0 <= r < 0.5) / len(realized), 4) if realized else None,
            "pct_reached_original_tp": round(sum(1 for o in subset if o.tp_hit) / n, 4) if n else None,
        })

    # The explicit +1R breakdown the roadmap asks for.
    at_least_1r = [o for o in outcomes if o.reached_1r]
    n1r = len(at_least_1r)
    breakdown_1r = None
    if n1r:
        realized_ge_1r = sum(1 for o in at_least_1r if (o.outcome_r or 0) >= 1.0)
        realized_0_5_to_1r = sum(1 for o in at_least_1r if 0.5 <= (o.outcome_r or 0) < 1.0)
        realized_0_to_0_5r = sum(1 for o in at_least_1r if 0 <= (o.outcome_r or 0) < 0.5)
        realized_negative = sum(1 for o in at_least_1r if (o.outcome_r or 0) < 0)
        breakdown_1r = {
            "n": n1r,
            "pct_realized_ge_1r": round(realized_ge_1r / n1r, 4),
            "pct_realized_0_5_to_1r": round(realized_0_5_to_1r / n1r, 4),
            "pct_realized_0_to_0_5r": round(realized_0_to_0_5r / n1r, 4),
            "pct_realized_negative": round(realized_negative / n1r, 4),
            "r_lost_vs_locking_1r_at_milestone": round(sum(1.0 - (o.outcome_r or 0) for o in at_least_1r if (o.outcome_r or 0) < 1.0), 4),
        }

    return {"anchor_strategy": anchor_strategy, "total_n": total_n, "funnel": stages, "at_least_1r_breakdown": breakdown_1r}


def _mfe_bucket(outcome: Any) -> str:
    if outcome.reached_2r:
        return LARGE_WINNER_BUCKET
    if outcome.reached_1r:
        return STRONG_WINNER
    if outcome.reached_0_5r:
        return DEVELOPING_WINNER
    if outcome.reached_0_25r:
        return WEAK_MOVE
    return IMMEDIATE_FAILURE_BUCKET


def entry_failure_vs_giveback(anchor_strategy: str) -> dict[str, Any]:
    """Phase 3 (profit-capture forensic study): buckets every trade by how far it got (MFE-based,
    independent of what it realized), sums each bucket's real contribution to total expectancy,
    then explicitly separates ENTRY_FAILURE_COST (R lost by trades that never got going at all)
    from PROFIT_GIVEBACK_COST (R given back by trades that DID get going but didn't keep it) --
    the central question this phase exists to answer, computed, not assumed."""
    rows = _trusted_rows(anchor_strategy=anchor_strategy)
    outcomes = [o for _fp, o in rows]
    total_n = len(outcomes)

    buckets: dict[str, list[Any]] = {IMMEDIATE_FAILURE_BUCKET: [], WEAK_MOVE: [], DEVELOPING_WINNER: [], STRONG_WINNER: [], LARGE_WINNER_BUCKET: []}
    for o in outcomes:
        buckets[_mfe_bucket(o)].append(o)

    bucket_summary = {}
    total_expectancy_r = sum((o.outcome_r or 0) for o in outcomes)
    for name, members in buckets.items():
        n = len(members)
        contribution_r = sum((o.outcome_r or 0) for o in members)
        bucket_summary[name] = {
            "n": n,
            "pct_of_all": round(n / total_n, 4) if total_n else None,
            "total_contribution_r": round(contribution_r, 4),
            "contribution_to_expectancy_pct": round(contribution_r / total_expectancy_r, 4) if total_expectancy_r else None,
            "avg_realized_r": _avg([o.outcome_r for o in members]),
        }

    entry_failure_cost = bucket_summary[IMMEDIATE_FAILURE_BUCKET]["total_contribution_r"]
    # Giveback = R given up between peak (mfe_r) and what was actually realized (outcome_r),
    # summed only over trades that reached at least +0.25R (i.e. excludes IMMEDIATE_FAILURE,
    # which never had anything to give back in the first place).
    moved_trades = [o for o in outcomes if o.reached_0_25r]
    profit_giveback_cost = round(sum((o.mfe_r or 0) - (o.outcome_r or 0) for o in moved_trades), 4)

    return {
        "anchor_strategy": anchor_strategy,
        "total_n": total_n,
        "total_expectancy_r": round(total_expectancy_r, 4),
        "buckets": bucket_summary,
        "entry_failure_cost_r": round(entry_failure_cost, 4),
        "profit_giveback_cost_r": profit_giveback_cost,
        "entry_failure_cost_pct_of_total_loss": round(entry_failure_cost / total_expectancy_r, 4) if total_expectancy_r < 0 else None,
        "profit_giveback_cost_pct_of_total_loss": round(-profit_giveback_cost / total_expectancy_r, 4) if total_expectancy_r < 0 else None,
    }


def milestone_continuation_probabilities(anchor_strategy: str) -> dict[str, Any]:
    """Phase 7: continuation/reversal probabilities conditional on having reached a given
    milestone -- real, measured, feeds Adaptive Manager protection-timing decisions."""
    rows = _trusted_rows(anchor_strategy=anchor_strategy)
    outcomes = [o for _fp, o in rows]

    def _given(milestone: str) -> list[Any]:
        return [o for o in outcomes if getattr(o, milestone)]

    result = {}
    for milestone, targets in (("reached_0_5r", ("reached_1r", "reached_1_5r")), ("reached_0_75r", ("reached_1r", "reached_2r"))):
        given = _given(milestone)
        n = len(given)
        if n == 0:
            result[milestone] = {"n": 0, "status": "INSUFFICIENT_SAMPLE"}
            continue
        reversal_to_loss = sum(1 for o in given if (o.outcome_r or 0) < 0)
        entry = {"n": n, "status": "OK", "probability_reversal_to_loss": round(reversal_to_loss / n, 4)}
        for target in targets:
            hit = sum(1 for o in given if getattr(o, target))
            entry[f"probability_{target}"] = round(hit / n, 4)
        result[milestone] = entry
    return {"anchor_strategy": anchor_strategy, "conditional_probabilities": result}


def feature_comparison(*, anchor_strategy: str | None = None, min_sample: int = _MIN_SAMPLE) -> dict[str, Any]:
    """The core Phase 1 question, measured directly: for each outcome category (WINNER / LOSER /
    IMMEDIATE_FAILURE / LARGE_WINNER), what fraction of real setups had each SMC/context feature
    present at entry, and what was the real distribution of session/regime/atr_regime/direction/
    confidence_band. A feature that differs meaningfully between WINNER and IMMEDIATE_FAILURE
    rates is evidence; one that doesn't is evidence the OPPOSITE direction just as validly."""
    rows = _trusted_rows(anchor_strategy=anchor_strategy)
    by_category: dict[str, list[Any]] = {WINNER: [], LOSER: [], IMMEDIATE_FAILURE: [], LARGE_WINNER: []}
    for fp, outcome in rows:
        by_category[_categorize(outcome)].append(fp)

    boolean_features = ("bos_present", "choch_present", "mss_present", "displacement_present", "liquidity_sweep_present")

    def _summarize(fingerprints: list[Any]) -> dict[str, Any]:
        n = len(fingerprints)
        if n < min_sample:
            return {"n": n, "status": "INSUFFICIENT_SAMPLE"}
        summary: dict[str, Any] = {"n": n, "status": "OK"}
        for feature in boolean_features:
            summary[f"pct_{feature}"] = round(sum(1 for fp in fingerprints if getattr(fp, feature)) / n, 4)
        for dim in ("session", "regime", "atr_regime", "spread_regime", "direction", "confidence_band"):
            counts: dict[str, int] = {}
            for fp in fingerprints:
                value = getattr(fp, dim, None)
                if value:
                    counts[str(value)] = counts.get(str(value), 0) + 1
            summary[f"distribution_{dim}"] = {k: round(v / n, 4) for k, v in sorted(counts.items(), key=lambda kv: -kv[1])}
        return summary

    return {"anchor_strategy": anchor_strategy, "categories": {category: _summarize(fps) for category, fps in by_category.items()}}
