"""Phase 7 (Forex/MT5 roadmap): regime/edge decay detection.

Distinct from walk_forward.py's Phase 1 (a ONE-TIME chronological train/OOS split proving a
strategy's edge held up historically): this module answers a different, ongoing question --
"is a strategy that ALREADY passed OOS validation losing its edge RIGHT NOW", by comparing its
long-term (all trusted history) statistics against its most recent 100/50/20-trade rolling
windows. A strategy can pass Phase 1 once and still decay months later as market regimes shift;
this is the module that would catch that.

Reuses walk_forward.py's `_stats_for_rows`/`_fetch_trusted_rows` (same trusted-outcome query,
same win_rate/expectancy_r/profit_factor/MFE-MAE/immediate-failure-rate computation) rather than
reimplementing statistics -- this module only adds the rolling-window comparison and
classification on top.

Statuses:
  INSUFFICIENT_RECENT_SAMPLE -- fewer than 20 long-term OR fewer than 20 recent trades. Never
                                 guesses at decay from a handful of trades.
  FAILED       -- no real long-term edge ever existed (long-term expectancy <= 0), OR the edge
                  existed but BOTH the 20- and 50-trade recent windows have gone non-positive
                  (persistent, not a single noisy window).
  DECAY_WARNING -- the 20-trade window has gone non-positive but the 50-trade window has not yet
                   (an early signal, not yet confirmed persistent), OR the 20-trade window
                   retains less than 30% of long-term expectancy.
  WEAKENING    -- the 20-trade window retains 30-70% of long-term expectancy.
  EDGE_STABLE  -- the 20-trade window retains at least 70% of long-term expectancy.

This module NEVER rewrites strategy logic and never disables a strategy by itself -- it only
computes a status. Trust-layer downgrade on FAILED/DECAY_WARNING is a decision for the caller
(entry_intelligence.py's trust-gating layer), exactly like walk_forward's edge_stability gate.

Execution-cost drift (also named in the roadmap) is deliberately NOT included here: ExecutionOrderORM
has no reliable join back to a specific historical outcome (see execution_quality.py's own
documented limitation), so a "cost-adjusted" comparison here would rest on an unverified linkage --
flagged as a remaining item rather than shipped as a fragile guess.
"""
from __future__ import annotations

from typing import Any

from backend.historical_intelligence.walk_forward import _fetch_trusted_rows, _stats_for_rows

EDGE_STABLE = "EDGE_STABLE"
WEAKENING = "WEAKENING"
DECAY_WARNING = "DECAY_WARNING"
FAILED = "FAILED"
INSUFFICIENT_RECENT_SAMPLE = "INSUFFICIENT_RECENT_SAMPLE"

_MIN_LONG_TERM_SAMPLE = 20
_MIN_RECENT_SAMPLE = 20
_WINDOWS = (100, 50, 20)


def classify_decay(*, long_term: dict[str, Any], recent_by_window: dict[int, dict[str, Any]], min_long_term: int = _MIN_LONG_TERM_SAMPLE, min_recent: int = _MIN_RECENT_SAMPLE) -> str:
    """Deterministic, transparent -- no ML, no opaque scoring (matches walk_forward's own
    classify_edge_stability philosophy). `recent_by_window` must contain at least windows 20
    and 50 (the two windows this classification actually keys off)."""
    recent_20 = recent_by_window.get(20)
    recent_50 = recent_by_window.get(50)
    if long_term["n"] < min_long_term or recent_20 is None or recent_20["n"] < min_recent or long_term["expectancy_r"] is None or recent_20["expectancy_r"] is None:
        return INSUFFICIENT_RECENT_SAMPLE
    if long_term["expectancy_r"] <= 0:
        return FAILED  # no real edge ever existed to decay from
    if recent_20["expectancy_r"] <= 0:
        recent_50_negative = recent_50 is not None and recent_50["n"] >= min_recent and recent_50["expectancy_r"] is not None and recent_50["expectancy_r"] <= 0
        return FAILED if recent_50_negative else DECAY_WARNING
    retained_fraction = recent_20["expectancy_r"] / long_term["expectancy_r"]
    if retained_fraction >= 0.7:
        return EDGE_STABLE
    if retained_fraction >= 0.3:
        return WEAKENING
    return DECAY_WARNING


def strategy_edge_decay(anchor_strategy: str, *, windows: tuple[int, ...] = _WINDOWS) -> dict[str, Any]:
    """Real query against the trusted historical outcome corpus for one strategy, chronologically
    ordered, comparing all-history ("long_term") statistics against each rolling recent window."""
    rows = _fetch_trusted_rows(anchor_strategy=anchor_strategy, canonical_symbol=None, regime=None, session=None, confidence_band=None, peer_group_hash=None)
    outcomes = [outcome for _fp, outcome in rows]
    long_term = _stats_for_rows(outcomes)
    recent_by_window = {window: _stats_for_rows(outcomes[-window:]) for window in windows}
    status = classify_decay(long_term=long_term, recent_by_window=recent_by_window)
    return {
        "anchor_strategy": anchor_strategy,
        "long_term": long_term,
        "recent": {f"last_{window}": stats for window, stats in recent_by_window.items()},
        "edge_decay_status": status,
    }


def all_strategies_edge_decay() -> dict[str, dict[str, Any]]:
    """Convenience driver -- one run per distinct anchor_strategy present in the trusted corpus."""
    from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM
    from backend.shared.db import SessionLocal

    with SessionLocal() as db:
        strategies = [row[0] for row in db.query(HistoricalPatternFingerprintORM.anchor_strategy).distinct().all()]
    return {strategy: strategy_edge_decay(strategy) for strategy in strategies}
