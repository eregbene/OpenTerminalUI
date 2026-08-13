"""Pattern statistics aggregation (Part 4) + sample reliability categorization (Part 5).

Reuses the statistical conventions already established in backend/brokers/mt5/
confidence_calibration.py and backend/mt5_strategies/analytics.py (win_rate/expectancy/
profit_factor/median R/sample_label) rather than inventing a parallel definition of any of
those terms. `reliability_label` below is a DELIBERATE EXTENSION of confidence_calibration.
sample_label's 4-tier scale into the 6-tier scale explicitly requested for Historical
Intelligence (UNTRUSTED/INSUFFICIENT/LOW_CONFIDENCE/PRELIMINARY/USEFUL/STRONG) -- the two
scales share the same 20/50/100 boundaries where they overlap, on purpose, so "preliminary"
means the same sample size in both systems.

Postgres remains authoritative -- this module always reads live from
historical_pattern_fingerprints JOIN historical_setup_outcomes. Redis caching of the RESULT of
these functions lives in cache.py, layered on top, never inside this module.
"""
from __future__ import annotations

import statistics as pystats
from typing import Any

from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM
from backend.shared.db import SessionLocal

RELIABILITY_LEVELS = ("UNTRUSTED", "INSUFFICIENT", "LOW_CONFIDENCE", "PRELIMINARY", "USEFUL", "STRONG")


def reliability_label(n: int, *, quality_ok_fraction: float = 1.0) -> str:
    """`quality_ok_fraction`: fraction of the sample sourced from trustworthy tiers (SNAPSHOT, or
    RECONSTRUCTED bars that passed quality validation and aren't proxy-only) -- a sample can be
    large but still UNTRUSTED if most of it is low-quality/proxy evidence (Part 22: never
    silently treat proxy or unvalidated data as equivalent to genuine evidence)."""
    if n < 5 or quality_ok_fraction < 0.5:
        return "UNTRUSTED"
    if n < 20:
        return "INSUFFICIENT"
    if n < 50:
        return "LOW_CONFIDENCE"
    if n < 100:
        return "PRELIMINARY"
    if n < 300:
        return "USEFUL"
    return "STRONG"


def _r_values(outcomes: list[HistoricalSetupOutcomeORM]) -> list[float]:
    return [o.outcome_r for o in outcomes if o.outcome_r is not None]


def _probability(outcomes: list[HistoricalSetupOutcomeORM], field: str) -> float | None:
    resolved = [o for o in outcomes if getattr(o, field) is not None]
    if not resolved:
        return None
    return round(sum(1 for o in resolved if getattr(o, field)) / len(resolved), 4)


def pattern_statistics(peer_group_hash: str, *, strategy_version: str | None = None, fingerprint_version: str | None = None) -> dict[str, Any]:
    """Aggregates every RESOLVED outcome sharing this peer_group_hash (optionally scoped to a
    specific strategy_version/fingerprint_version -- omit to aggregate across all versions,
    which callers should generally NOT do for anything feeding a live decision, per the
    versioning requirement that strategy-logic changes must not silently mix statistics)."""
    with SessionLocal() as db:
        query = (
            db.query(HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM)
            .join(HistoricalSetupOutcomeORM, HistoricalSetupOutcomeORM.fingerprint_id == HistoricalPatternFingerprintORM.fingerprint_id)
            .filter(HistoricalPatternFingerprintORM.peer_group_hash == peer_group_hash)
        )
        if strategy_version:
            query = query.filter(HistoricalPatternFingerprintORM.strategy_version == strategy_version)
        if fingerprint_version:
            query = query.filter(HistoricalPatternFingerprintORM.fingerprint_version == fingerprint_version)
        rows = query.all()

    fingerprints = [r[0] for r in rows]
    all_outcomes = [r[1] for r in rows]
    # UNTRUSTED-quality outcomes (outcomes.py) are excluded from every active aggregate below --
    # they are RESOLVED in the sense that a TP/SL touch was found, but the underlying future
    # candles could not be trusted (unfinalized, or a proven post-finalization anomaly), so they
    # must never contribute to a statistic that could influence a live decision.
    resolved = [o for o in all_outcomes if o.resolution_status == "RESOLVED" and o.data_quality != "UNTRUSTED"]
    untrusted_excluded = sum(1 for o in all_outcomes if o.resolution_status == "RESOLVED" and o.data_quality == "UNTRUSTED")
    n = len(resolved)

    trustworthy = sum(1 for fp in fingerprints if fp.source_quality_tier == "SNAPSHOT" or (fp.source_quality_tier == "RECONSTRUCTED" and not fp.proxy))
    quality_ok_fraction = (trustworthy / len(fingerprints)) if fingerprints else 0.0

    r_values = _r_values(resolved)
    net_r_values = [o.net_outcome_r for o in resolved if o.net_outcome_r is not None]
    wins = [r for r in r_values if r > 0]
    losses = [r for r in r_values if r < 0]
    gross_win = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    mfe_values = [o.mfe_r for o in resolved if o.mfe_r is not None]
    mae_values = [o.mae_r for o in resolved if o.mae_r is not None]
    holding = [o.holding_duration_seconds for o in resolved if o.holding_duration_seconds is not None]
    immediate_failures = [o for o in resolved if o.immediate_failure is not None]

    downside = sorted(r for r in r_values if r < 0)
    tail_5pct_count = max(1, round(len(downside) * 0.05)) if downside else 0
    downside_tail = downside[:tail_5pct_count] if downside else []

    source_tier_counts: dict[str, int] = {}
    for fp in fingerprints:
        source_tier_counts[fp.source_quality_tier] = source_tier_counts.get(fp.source_quality_tier, 0) + 1
    data_quality_counts: dict[str, int] = {}
    for o in all_outcomes:
        if o.resolution_status == "RESOLVED":
            data_quality_counts[o.data_quality] = data_quality_counts.get(o.data_quality, 0) + 1

    return {
        "peer_group_hash": peer_group_hash,
        "sample_size": n,
        "total_fingerprints": len(fingerprints),
        "pending_count": sum(1 for o in all_outcomes if o.resolution_status == "PENDING"),
        "untrusted_excluded_count": untrusted_excluded,
        "data_quality_counts": data_quality_counts,
        "reliability": reliability_label(n, quality_ok_fraction=quality_ok_fraction),
        "quality_ok_fraction": round(quality_ok_fraction, 4),
        "source_tier_counts": source_tier_counts,
        "win_rate": round(len(wins) / n, 4) if n else None,
        "expectancy_r": round(pystats.fmean(r_values), 4) if r_values else None,
        "median_r": round(pystats.median(r_values), 4) if r_values else None,
        "net_expectancy_r": round(pystats.fmean(net_r_values), 4) if net_r_values else None,
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else None,
        "median_mfe_r": round(pystats.median(mfe_values), 4) if mfe_values else None,
        "median_mae_r": round(pystats.median(mae_values), 4) if mae_values else None,
        "immediate_failure_rate": round(sum(1 for o in immediate_failures if o.immediate_failure) / len(immediate_failures), 4) if immediate_failures else None,
        "probability_0_5r": _probability(resolved, "reached_0_5r"),
        "probability_0_75r": _probability(resolved, "reached_0_75r"),
        "probability_1r": _probability(resolved, "reached_1r"),
        "probability_1_5r": _probability(resolved, "reached_1_5r"),
        "probability_2r": _probability(resolved, "reached_2r"),
        "avg_holding_seconds": round(pystats.fmean(holding), 1) if holding else None,
        "downside_tail_avg_r": round(pystats.fmean(downside_tail), 4) if downside_tail else None,
        "worst_r": min(r_values) if r_values else None,
    }
