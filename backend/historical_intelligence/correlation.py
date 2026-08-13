"""Phase 6 (Forex/MT5 roadmap): strategy/symbol/regime return correlation analytics.

Analytics-only, exactly as specified: this module NEVER disables a strategy, NEVER changes
sizing, and is not consulted by any live decision path -- it only answers "do these two
groups' returns tend to move together", which matters for capital-allocation risk (two
uncorrelated-by-signal strategies can still lose together if they're both long the same
currency exposure during the same regime) even though it says nothing about which group is
individually profitable (see walk_forward.py / statistics.py for that).

Return series construction: the trusted (non-UNTRUSTED, RESOLVED) historical outcome corpus is
grouped by (group_value, calendar day), and each day's outcome_r values are averaged into one
number -- a daily mean-return series per group, the standard unit for a return-correlation
matrix. Pairwise Pearson correlation is computed only over days where BOTH groups in the pair
have at least one observation (shared-day overlap), and a pair is only reported once at least
MIN_OVERLAP_DAYS is met -- a correlation computed from a handful of coincidentally-overlapping
days is not evidence of anything.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

import numpy as np

MIN_OVERLAP_DAYS = 10
HIGH_CORRELATION_THRESHOLD = 0.7

GROUP_STRATEGY = "anchor_strategy"
GROUP_SYMBOL = "canonical_symbol"
GROUP_REGIME = "regime_broad"


def daily_return_series(group_by: str) -> dict[str, dict[date, float]]:
    """Real query: every trusted, resolved outcome in the corpus, grouped by `group_by`
    (anchor_strategy / canonical_symbol / regime_broad) and by calendar day of entry_time,
    averaged within each (group, day) cell."""
    if group_by not in {GROUP_STRATEGY, GROUP_SYMBOL, GROUP_REGIME}:
        raise ValueError(f"unsupported group_by: {group_by}")

    from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM
    from backend.shared.db import SessionLocal

    column = getattr(HistoricalPatternFingerprintORM, group_by)
    with SessionLocal() as db:
        rows = (
            db.query(column, HistoricalPatternFingerprintORM.entry_time, HistoricalSetupOutcomeORM.outcome_r)
            .join(HistoricalSetupOutcomeORM, HistoricalSetupOutcomeORM.fingerprint_id == HistoricalPatternFingerprintORM.fingerprint_id)
            .filter(HistoricalSetupOutcomeORM.resolution_status == "RESOLVED", HistoricalSetupOutcomeORM.data_quality != "UNTRUSTED", HistoricalSetupOutcomeORM.outcome_r.isnot(None))
            .all()
        )

    buckets: dict[str, dict[date, list[float]]] = defaultdict(lambda: defaultdict(list))
    for group_value, entry_time, outcome_r in rows:
        if group_value is None or entry_time is None:
            continue
        buckets[str(group_value)][entry_time.date()].append(float(outcome_r))

    return {group_value: {day: sum(values) / len(values) for day, values in days.items()} for group_value, days in buckets.items()}


def _pearson(a: dict[date, float], b: dict[date, float]) -> tuple[float | None, int]:
    shared_days = sorted(set(a) & set(b))
    if len(shared_days) < MIN_OVERLAP_DAYS:
        return None, len(shared_days)
    x = np.array([a[day] for day in shared_days], dtype=float)
    y = np.array([b[day] for day in shared_days], dtype=float)
    if np.std(x) == 0 or np.std(y) == 0:
        return None, len(shared_days)  # a constant series has no defined correlation
    return round(float(np.corrcoef(x, y)[0, 1]), 4), len(shared_days)


def correlation_matrix(group_by: str) -> dict[str, Any]:
    series = daily_return_series(group_by)
    group_values = sorted(series)
    pairs: list[dict[str, Any]] = []
    for i, a in enumerate(group_values):
        for b in group_values[i + 1 :]:
            corr, overlap_days = _pearson(series[a], series[b])
            pairs.append({"a": a, "b": b, "correlation": corr, "overlap_days": overlap_days, "high_correlation": corr is not None and abs(corr) >= HIGH_CORRELATION_THRESHOLD})
    return {
        "group_by": group_by,
        "groups": group_values,
        "sample_days_per_group": {group: len(days) for group, days in series.items()},
        "pairs": pairs,
        "high_correlation_pairs": [p for p in pairs if p["high_correlation"]],
    }


def full_report() -> dict[str, Any]:
    """One call covering all three dimensions the roadmap asks for -- pairwise strategy, symbol,
    and regime correlation. Purely additive analytics; nothing here writes anything."""
    return {
        "by_strategy": correlation_matrix(GROUP_STRATEGY),
        "by_symbol": correlation_matrix(GROUP_SYMBOL),
        "by_regime": correlation_matrix(GROUP_REGIME),
    }
