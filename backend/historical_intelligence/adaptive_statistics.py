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


def _collect_rows() -> list[dict[str, Any]]:
    with SessionLocal() as db:
        events = db.query(AdaptiveManagementEventORM).all()
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
