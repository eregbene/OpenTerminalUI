"""Adaptive Historical Intelligence chronological walk-forward / calibration validation
(Adaptive-Historical-Intelligence-Backfill directive, Phase 16/17).

Mirrors historical_intelligence/walk_forward.py's TRAIN -> (purge) -> OOS design exactly --
REUSES its classify_edge_stability()/EDGE_* constants/_PURGE_WINDOW rather than reimplementing
them -- applied to HistoricalAdaptiveStateORM/HistoricalAdaptiveOutcomeORM (state_time as the
chronological axis) instead of HistoricalPatternFingerprintORM/HistoricalSetupOutcomeORM
(entry_time). "expectancy_r" for the entry side becomes "expected_additional_r" here -- the
natural continuous-outcome analog for a mid-trade profit-retention decision (there is no single
final-R outcome at a state; the state's forward-looking additional R is the comparable evidence),
recast into classify_edge_stability's existing {n, expectancy_r} shape so the SAME, already-
validated train-vs-OOS retention-fraction logic runs unmodified.

Calibration (Phase 17): the TRAIN split's aggregate outcome rates (P(continue to +1R),
P(round-trip), P(reach original TP)) are treated as the model's prediction and compared against
the OOS split's actual rates for the SAME milestone/filter -- if TRAIN says 70% of R_0_50 states
continued, roughly 70% of OOS R_0_50 states should have too. No leakage: OOS rows never
contribute to the prediction being tested against them. This is a coarser test than a full
per-state predicted-probability decile curve (which the current single-strategy, ~8k-row corpus
is too thin to support meaningfully -- see run_adaptive_walk_forward_by_milestone's real results),
but it is the same level of rigor walk_forward.py itself already applies on the entry side.

Read-only over Postgres; nothing here persists (unlike walk_forward.py's
HistoricalWalkForwardResultORM) -- this is a periodic/on-demand validation pass over the backfill
corpus, not a per-cycle live lookup, so there is no live caller needing a fast persisted read.
"""
from __future__ import annotations

import statistics as pystats
from typing import Any

from backend.historical_intelligence.orm import HistoricalAdaptiveOutcomeORM, HistoricalAdaptiveStateORM
from backend.historical_intelligence.walk_forward import _PURGE_WINDOW, EDGE_INSUFFICIENT_SAMPLE, classify_edge_stability
from backend.shared.db import SessionLocal

_MIN_TRAIN_SAMPLE = 20
_MIN_OOS_SAMPLE = 10


def _stats_for_outcomes(outcomes: list[HistoricalAdaptiveOutcomeORM]) -> dict[str, Any]:
    n = len(outcomes)
    if n == 0:
        return {
            "n": 0, "probability_continue_plus_1r": None, "probability_round_trip": None,
            "probability_reach_tp": None, "probability_reversal": None, "probability_hit_sl": None,
            "expected_additional_r": None, "median_additional_r": None,
        }

    def _rate(attr: str) -> float | None:
        vals = [getattr(o, attr) for o in outcomes if getattr(o, attr) is not None]
        return round(sum(1 for v in vals if v) / len(vals), 4) if vals else None

    additional_r = [o.post_exit_additional_r_available for o in outcomes if o.post_exit_additional_r_available is not None]
    round_trip_pairs = [(bool(o.post_exit_reached_plus_1r), bool(o.post_exit_reversed_strongly)) for o in outcomes if o.post_exit_reached_plus_1r is not None and o.post_exit_reversed_strongly is not None]
    round_trip_rate = round(sum(1 for a, b in round_trip_pairs if a and b) / len(round_trip_pairs), 4) if round_trip_pairs else None

    return {
        "n": n,
        "probability_continue_plus_1r": _rate("post_exit_reached_plus_1r"),
        "probability_reach_tp": _rate("post_exit_reached_original_tp"),
        "probability_reversal": _rate("post_exit_reversed_strongly"),
        "probability_hit_sl": _rate("post_exit_would_have_hit_original_sl"),
        "probability_round_trip": round_trip_rate,
        "expected_additional_r": round(pystats.fmean(additional_r), 4) if additional_r else None,
        "median_additional_r": round(pystats.median(additional_r), 4) if additional_r else None,
    }


def classify_adaptive_edge_stability(*, train: dict[str, Any], oos: dict[str, Any], min_train: int = _MIN_TRAIN_SAMPLE, min_oos: int = _MIN_OOS_SAMPLE) -> str:
    train_proxy = {"n": train["n"], "expectancy_r": train["expected_additional_r"]}
    oos_proxy = {"n": oos["n"], "expectancy_r": oos["expected_additional_r"]}
    return classify_edge_stability(train=train_proxy, oos=oos_proxy, min_train=min_train, min_oos=min_oos)


def _fetch_rows(*, strategy: str | None, symbol: str | None, direction: str | None, milestone_label: str | None) -> list[tuple[HistoricalAdaptiveStateORM, HistoricalAdaptiveOutcomeORM]]:
    with SessionLocal() as db:
        q = (
            db.query(HistoricalAdaptiveStateORM, HistoricalAdaptiveOutcomeORM)
            .join(HistoricalAdaptiveOutcomeORM, HistoricalAdaptiveOutcomeORM.state_id == HistoricalAdaptiveStateORM.state_id)
            .filter(HistoricalAdaptiveOutcomeORM.post_exit_status == "RESOLVED")
        )
        if strategy:
            q = q.filter(HistoricalAdaptiveStateORM.strategy == strategy)
        if symbol:
            q = q.filter(HistoricalAdaptiveStateORM.canonical_symbol == symbol.upper())
        if direction:
            q = q.filter(HistoricalAdaptiveStateORM.direction == direction.upper())
        if milestone_label:
            q = q.filter(HistoricalAdaptiveStateORM.milestone_label == milestone_label)
        rows = q.order_by(HistoricalAdaptiveStateORM.state_time.asc()).all()
    return rows


def run_adaptive_walk_forward(*, strategy: str | None = None, symbol: str | None = None, direction: str | None = None, milestone_label: str | None = None, train_fraction: float = 0.6, purge=_PURGE_WINDOW) -> dict[str, Any]:
    """TRAIN -> (purge) -> OOS over the adaptive-state corpus, chronological by state_time, no
    shuffling, no leakage. purge defaults to walk_forward.py's OWN _PURGE_WINDOW (12h) -- the
    same documented, conservative trade-off already accepted on the entry side, appropriate here
    too since adaptive_backfill.py's _MAX_LOOKFORWARD_BARS=800 at the historical corpus's 15-
    minute stride caps outcome resolution at the SAME ~8.3-day horizon walk_forward.py's own
    docstring already reasons about."""
    rows = _fetch_rows(strategy=strategy, symbol=symbol, direction=direction, milestone_label=milestone_label)
    total_n = len(rows)
    if total_n == 0:
        empty = _stats_for_outcomes([])
        return {"edge_stability": EDGE_INSUFFICIENT_SAMPLE, "total_n": 0, "purged_n": 0, "train": empty, "oos": empty, "calibration": {}, "filters": _filters(strategy, symbol, direction, milestone_label)}

    split_index = max(1, min(total_n - 1, int(total_n * train_fraction)))
    split_time = rows[split_index][0].state_time

    train_rows = [o for s, o in rows if s.state_time <= split_time - purge]
    oos_rows = [o for s, o in rows if s.state_time > split_time + purge]
    purged_n = total_n - len(train_rows) - len(oos_rows)

    train_stats = _stats_for_outcomes(train_rows)
    oos_stats = _stats_for_outcomes(oos_rows)
    edge_stability = classify_adaptive_edge_stability(train=train_stats, oos=oos_stats)

    degradation_pct = None
    if train_stats["expected_additional_r"] not in (None, 0) and oos_stats["expected_additional_r"] is not None:
        degradation_pct = round(100.0 * (1.0 - oos_stats["expected_additional_r"] / train_stats["expected_additional_r"]), 1)

    calibration: dict[str, Any] = {}
    for prob_key in ("probability_continue_plus_1r", "probability_round_trip", "probability_reach_tp", "probability_reversal"):
        predicted = train_stats.get(prob_key)
        actual = oos_stats.get(prob_key)
        calibration[prob_key] = {
            "train_predicted": predicted, "oos_actual": actual,
            "abs_calibration_error": round(abs(predicted - actual), 4) if predicted is not None and actual is not None else None,
        }

    return {
        "edge_stability": edge_stability, "total_n": total_n, "purged_n": purged_n,
        "split_time": split_time.isoformat(), "train": train_stats, "oos": oos_stats,
        "degradation_pct": degradation_pct, "calibration": calibration,
        "filters": _filters(strategy, symbol, direction, milestone_label),
    }


def _filters(strategy, symbol, direction, milestone_label) -> dict[str, Any]:
    return {"strategy": strategy, "symbol": symbol, "direction": direction, "milestone_label": milestone_label}


def run_adaptive_walk_forward_by_milestone(*, strategy: str | None = None) -> dict[str, dict[str, Any]]:
    """Convenience driver: one walk-forward run per milestone_label currently represented in the
    adaptive backfill corpus (optionally scoped to one strategy) -- the directive's explicit
    "+0.25R/+0.5R/+0.75R/+1R" per-milestone analysis requirement."""
    with SessionLocal() as db:
        q = db.query(HistoricalAdaptiveStateORM.milestone_label).distinct()
        if strategy:
            q = q.filter(HistoricalAdaptiveStateORM.strategy == strategy)
        milestones = [r[0] for r in q.all() if r[0]]
    return {m: run_adaptive_walk_forward(strategy=strategy, milestone_label=m) for m in milestones}
