"""Adaptive Manager historical trade-evolution statistics (Part 16).

Aggregates AdaptiveManagementEventORM (real past management-cycle snapshots) joined to
AdaptiveManagerCounterfactualORM's post-exit resolution (the ALREADY-EXISTING, previously-fixed
post-exit resolver -- see backend/adaptive_management/outcome_resolver.py) by peer group, per
adaptive_fingerprint.py's state hash. PENDING/UNRESOLVABLE_NO_DATA post-exit rows are always
excluded from the resolved sample -- Part 16's explicit "do not treat PENDING outcomes as
resolved" requirement, enforced at the single filter point below so no downstream aggregate can
accidentally include them.

Computed in Python over an in-memory join rather than a SQL aggregate (unlike statistics.py's
entry-side pattern_statistics, which uses an indexed peer_group_hash column) -- adaptive event
volume is materially smaller than candidate-evaluation volume, and this avoids adding yet another
persisted, indexed fingerprint table for a state space that's cheap to recompute on demand.
"""
from __future__ import annotations

import statistics as pystats
from collections import defaultdict
from typing import Any

from backend.adaptive_management.orm import AdaptiveManagementEventORM, AdaptiveManagerCounterfactualORM, AdaptivePositionBaselineORM
from backend.historical_intelligence.adaptive_fingerprint import build_state_fingerprint
from backend.historical_intelligence.statistics import reliability_label
from backend.shared.db import SessionLocal


_MAX_HISTORICAL_ROWS_PER_QUERY = 3000


def _collect_historical_rows(*, strategy: str | None = None, symbol: str | None = None, direction: str | None = None, limit: int = _MAX_HISTORICAL_ROWS_PER_QUERY) -> list[dict[str, Any]]:
    """Historical-backfill counterpart to _collect_rows (Adaptive-Historical-Intelligence-
    Backfill directive, Phase 1/7): reads HistoricalAdaptiveStateORM/HistoricalAdaptiveOutcomeORM
    (built by adaptive_backfill.py from the entry-side replay corpus) rather than real managed
    positions. Unlike _collect_rows, the hard-match filter (strategy/symbol/direction) is pushed
    down to SQL -- the historical corpus is orders of magnitude larger than the real-position
    table _collect_rows was designed around, so loading it unfiltered into Python on every call
    would not scale. Returns the SAME row shape ({"fields", "counterfactual"} plus
    "source_trade_id" for same-trade dedup) so callers can merge real and historical neighbors
    through IDENTICAL downstream code.

    `limit` (Part 21 -- real, measured finding: an unbounded query against a corpus of tens of
    thousands of states took 16.4s, unacceptable for a live per-cycle call) bounds the raw
    candidate pool to the `limit` MOST RECENT matching states, ordered by state_time DESC. This
    caps worst-case latency independent of how large the corpus grows -- top_k neighbor selection
    (50-200) never needed every historical state that ever existed, only a large-enough,
    reasonably representative pool to find its best matches from."""
    from backend.historical_intelligence.orm import HistoricalAdaptiveOutcomeORM, HistoricalAdaptiveStateORM

    with SessionLocal() as db:
        q = db.query(HistoricalAdaptiveStateORM)
        if strategy is not None:
            q = q.filter(HistoricalAdaptiveStateORM.strategy == strategy)
        if symbol is not None:
            q = q.filter(HistoricalAdaptiveStateORM.canonical_symbol == symbol.upper())
        if direction is not None:
            q = q.filter(HistoricalAdaptiveStateORM.direction == direction.upper())
        q = q.order_by(HistoricalAdaptiveStateORM.state_time.desc()).limit(limit)
        states = q.all()
        if not states:
            return []
        state_ids = [s.state_id for s in states]
        outcomes_by_state = {o.state_id: o for o in db.query(HistoricalAdaptiveOutcomeORM).filter(HistoricalAdaptiveOutcomeORM.state_id.in_(state_ids)).all()}

    rows: list[dict[str, Any]] = []
    for state in states:
        fields = build_state_fingerprint(
            strategy=state.strategy, symbol=state.canonical_symbol, direction=state.direction,
            original_regime=state.original_regime, current_regime=state.current_regime, current_r=state.current_r,
            max_achieved_r=state.max_achieved_r, min_achieved_r=state.min_achieved_r, elapsed_seconds=state.elapsed_seconds,
            is_at_or_beyond_breakeven=state.is_at_or_beyond_breakeven, is_trailing_action=state.is_trailing_action, now=state.state_time,
        )
        # Extra dimensions (Phase 7's high-weight list: giveback, structure state, BOS/CHoCH/MSS)
        # that build_state_fingerprint doesn't know about -- added alongside its own output
        # rather than changing that shared, entry-agnostic function's contract.
        fields["giveback_bucket"] = _bucket_giveback(state.giveback_from_mfe_r)
        fields["structure_intact"] = state.structure_intact
        fields["structure_against_trade"] = bool(state.bos_against_trade or state.choch_against_trade or state.mss_against_trade)
        fields["atr_regime"] = state.atr_regime
        counterfactual = outcomes_by_state.get(state.state_id)
        rows.append({"fields": fields, "counterfactual": counterfactual, "source_trade_id": state.source_fingerprint_id, "state_time": state.state_time})
    return rows


def _bucket_giveback(giveback: float | None) -> str | None:
    if giveback is None:
        return None
    if giveback < 0.1:
        return "MINIMAL"
    if giveback < 0.25:
        return "MODERATE"
    return "SIGNIFICANT"


_MAX_REAL_EVENTS_PER_QUERY = 2000


def _collect_rows(*, limit: int = _MAX_REAL_EVENTS_PER_QUERY) -> list[dict[str, Any]]:
    """Real, measured finding (Adaptive-Historical-Intelligence-Backfill directive, Part 21): an
    unbounded `.all()` here took 6.46s against 31,279 real AdaptiveManagementEventORM rows (the
    append-only per-cycle-per-position journal, accumulated over the whole engagement's real
    DEMO trading history) -- this function is called on EVERY real Adaptive Manager cycle for
    EVERY managed position (via evaluate_adaptive_intelligence -> state_statistics), so this cost
    was already being paid live, silently, before this fix (a pre-existing bug, not something
    the historical-backfill work introduced -- only newly measured because of it). Bounding to
    the `limit` most recent events preserves this function's exact existing semantics (still
    includes PENDING states, not just RESOLVED ones -- state_statistics's total_states_observed
    metric is unchanged) while capping worst-case latency independent of how large the real
    event journal grows."""
    with SessionLocal() as db:
        events = db.query(AdaptiveManagementEventORM).order_by(AdaptiveManagementEventORM.created_at.desc()).limit(limit).all()
        baselines = {b.position_id: b for b in db.query(AdaptivePositionBaselineORM).all()}
        counterfactuals = {c.position_id: c for c in db.query(AdaptiveManagerCounterfactualORM).all()}

    rows: list[dict[str, Any]] = []
    for event in events:
        baseline = baselines.get(event.position_id)
        counterfactual = counterfactuals.get(event.position_id)
        elapsed_seconds = None
        if baseline is not None and event.created_at and baseline.created_at:
            elapsed_seconds = (event.created_at - baseline.created_at).total_seconds()
        fields = build_state_fingerprint(
            strategy=event.strategy or (baseline.original_strategy if baseline else None),
            symbol=event.symbol, direction=event.direction,
            # AdaptivePositionBaselineORM has no regime column at all -- there is no honest
            # value to supply here (using original_strategy as a stand-in would silently
            # conflate two unrelated dimensions), so this is always None until/unless the
            # baseline capture is extended to record entry-time regime.
            original_regime=None,
            current_regime=event.market_regime, current_r=event.current_r,
            max_achieved_r=event.max_achieved_r, min_achieved_r=event.min_achieved_r,
            elapsed_seconds=elapsed_seconds, is_at_or_beyond_breakeven=event.is_at_or_beyond_breakeven,
            is_trailing_action=event.is_trailing_action, now=event.created_at,
        )
        rows.append({"fields": fields, "event": event, "counterfactual": counterfactual})
    return rows


def state_statistics(peer_group_hash: str) -> dict[str, Any]:
    """Aggregates every historical open-trade STATE matching `peer_group_hash` whose post-exit
    outcome is RESOLVED (never PENDING/UNRESOLVABLE_NO_DATA -- filtered here, the one place this
    matters). Answers Part 16/18's explicit questions: probability of reaching the original TP /
    further R milestones from THIS state, probability of reversal/round-trip, expected
    additional R, median remaining MFE/future MAE, average time to continuation/reversal."""
    all_rows = _collect_rows()
    matching = [r for r in all_rows if r["fields"]["peer_group_hash"] == peer_group_hash]
    total_states = len(matching)

    resolved = [r for r in matching if r["counterfactual"] is not None and r["counterfactual"].post_exit_status == "RESOLVED"]
    n = len(resolved)

    reach_tp = [r["counterfactual"].post_exit_reached_original_tp for r in resolved if r["counterfactual"].post_exit_reached_original_tp is not None]
    reach_1r = [r["counterfactual"].post_exit_reached_plus_1r for r in resolved if r["counterfactual"].post_exit_reached_plus_1r is not None]
    reversed_strongly = [r["counterfactual"].post_exit_reversed_strongly for r in resolved if r["counterfactual"].post_exit_reversed_strongly is not None]
    hit_sl = [r["counterfactual"].post_exit_would_have_hit_original_sl for r in resolved if r["counterfactual"].post_exit_would_have_hit_original_sl is not None]
    # Round-trip proxy (Part 16 -- no dedicated field exists upstream): reached +1R favorable
    # AND later reversed strongly -- "gave back a real gain", the closest honest approximation
    # available from the existing resolver's own booleans. Documented as an approximation, not
    # invented outcome tracking.
    round_trip = [bool(r["counterfactual"].post_exit_reached_plus_1r) and bool(r["counterfactual"].post_exit_reversed_strongly) for r in resolved if r["counterfactual"].post_exit_reached_plus_1r is not None and r["counterfactual"].post_exit_reversed_strongly is not None]

    additional_r = [r["counterfactual"].post_exit_additional_r_available for r in resolved if r["counterfactual"].post_exit_additional_r_available is not None]
    mfe_values = [r["counterfactual"].post_exit_mfe_r for r in resolved if r["counterfactual"].post_exit_mfe_r is not None]
    mae_values = [r["counterfactual"].post_exit_mae_r for r in resolved if r["counterfactual"].post_exit_mae_r is not None]
    time_to_continuation = [r["counterfactual"].post_exit_time_to_continuation_seconds for r in resolved if r["counterfactual"].post_exit_time_to_continuation_seconds is not None]
    time_to_reversal = [r["counterfactual"].post_exit_time_to_reversal_seconds for r in resolved if r["counterfactual"].post_exit_time_to_reversal_seconds is not None]

    classification_counts: dict[str, int] = defaultdict(int)
    for r in resolved:
        classification_counts[r["counterfactual"].post_exit_classification] += 1

    def _prob(values: list[bool]) -> float | None:
        return round(sum(1 for v in values if v) / len(values), 4) if values else None

    return {
        "peer_group_hash": peer_group_hash,
        "total_states_observed": total_states,
        "resolved_sample_size": n,
        "reliability": reliability_label(n),
        "probability_reach_original_tp": _prob(reach_tp),
        "probability_reach_plus_1r": _prob(reach_1r),
        "probability_reversal": _prob(reversed_strongly),
        "probability_hit_sl": _prob(hit_sl),
        "probability_round_trip": _prob(round_trip),
        "expected_additional_r": round(pystats.fmean(additional_r), 4) if additional_r else None,
        "median_remaining_mfe_r": round(pystats.median(mfe_values), 4) if mfe_values else None,
        "median_future_mae_r": round(pystats.median(mae_values), 4) if mae_values else None,
        "avg_time_to_continuation_seconds": round(pystats.fmean(time_to_continuation), 1) if time_to_continuation else None,
        "avg_time_to_reversal_seconds": round(pystats.fmean(time_to_reversal), 1) if time_to_reversal else None,
        "post_exit_classification_counts": dict(classification_counts),
    }


def all_peer_group_hashes() -> list[str]:
    rows = _collect_rows()
    return sorted({r["fields"]["peer_group_hash"] for r in rows})
