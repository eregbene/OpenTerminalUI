"""Phase 1 inventory: real adaptive-state coverage by strategy/symbol/direction/milestone/
regime/session. Reports RAW N (row counts, cheap group-by), INDEPENDENT N (same-trade +
temporal-cluster dedup applied to the whole segment population, not just a query's neighbor
pool -- reuses adaptive_similarity._cluster_and_dedup_states unchanged), RESOLVED OUTCOMES, and
a sampled EFFECTIVE N (median/max over real weighted_state_statistics queries against the most
populous strategy/symbol/direction/milestone combinations) -- effective N is inherently
query-dependent (a similarity-weighted sum against one specific state), so a single aggregate
number doesn't exist the way raw/independent N do; this reports the real, sampled distribution
instead of fabricating one number.
"""
from __future__ import annotations

import statistics as pystats

from sqlalchemy import func

from backend.historical_intelligence import adaptive_cache
from backend.historical_intelligence.adaptive_fingerprint import build_state_fingerprint
from backend.historical_intelligence.adaptive_similarity import _cluster_and_dedup_states
from backend.historical_intelligence.orm import HistoricalAdaptiveOutcomeORM, HistoricalAdaptiveStateORM
from backend.shared.db import SessionLocal


def _print_table(title: str, rows: list[tuple]) -> None:
    print(f"\n=== {title} ===", flush=True)
    for r in rows:
        print(r, flush=True)


def main() -> None:
    with SessionLocal() as db:
        by_strategy = db.query(HistoricalAdaptiveStateORM.strategy, func.count(HistoricalAdaptiveStateORM.state_id)).group_by(HistoricalAdaptiveStateORM.strategy).order_by(func.count(HistoricalAdaptiveStateORM.state_id).desc()).all()
        by_symbol_dir = db.query(HistoricalAdaptiveStateORM.strategy, HistoricalAdaptiveStateORM.canonical_symbol, HistoricalAdaptiveStateORM.direction, func.count(HistoricalAdaptiveStateORM.state_id)).group_by(HistoricalAdaptiveStateORM.strategy, HistoricalAdaptiveStateORM.canonical_symbol, HistoricalAdaptiveStateORM.direction).order_by(func.count(HistoricalAdaptiveStateORM.state_id).desc()).limit(40).all()
        by_milestone = db.query(HistoricalAdaptiveStateORM.milestone_label, func.count(HistoricalAdaptiveStateORM.state_id)).group_by(HistoricalAdaptiveStateORM.milestone_label).order_by(func.count(HistoricalAdaptiveStateORM.state_id).desc()).all()
        by_regime = db.query(HistoricalAdaptiveStateORM.current_regime, func.count(HistoricalAdaptiveStateORM.state_id)).group_by(HistoricalAdaptiveStateORM.current_regime).order_by(func.count(HistoricalAdaptiveStateORM.state_id).desc()).all()
        by_session = db.query(HistoricalAdaptiveStateORM.session, func.count(HistoricalAdaptiveStateORM.state_id)).group_by(HistoricalAdaptiveStateORM.session).order_by(func.count(HistoricalAdaptiveStateORM.state_id).desc()).all()
        total_states = db.query(func.count(HistoricalAdaptiveStateORM.state_id)).scalar()
        total_resolved = db.query(func.count(HistoricalAdaptiveOutcomeORM.outcome_id)).filter(HistoricalAdaptiveOutcomeORM.post_exit_status == "RESOLVED").scalar()

    print(f"TOTAL_STATES={total_states} TOTAL_RESOLVED={total_resolved}", flush=True)
    _print_table("By strategy", by_strategy)
    _print_table("By milestone", by_milestone)
    _print_table("By regime", by_regime)
    _print_table("By session", by_session)
    _print_table("Top 40 (strategy, symbol, direction)", by_symbol_dir)

    print("\n=== Independent N per top (strategy, symbol, direction) segment (same-trade + 6h temporal-cluster dedup) ===", flush=True)
    for strategy, symbol, direction, raw_n in by_symbol_dir[:20]:
        with SessionLocal() as db:
            states = db.query(HistoricalAdaptiveStateORM).filter(HistoricalAdaptiveStateORM.strategy == strategy, HistoricalAdaptiveStateORM.canonical_symbol == symbol, HistoricalAdaptiveStateORM.direction == direction).all()
        neighbors = [{"trade_id": f"HIST:{s.source_fingerprint_id}", "state_time": s.state_time, "similarity": 1.0, "fields": {"symbol": s.canonical_symbol, "direction": s.direction, "strategy": s.strategy}} for s in states]
        independent = _cluster_and_dedup_states(neighbors)
        print(f"{strategy} {symbol} {direction}: raw_n={raw_n} independent_n={len(independent)}", flush=True)

    print("\n=== Sampled real effective N (weighted_state_statistics) across top segments x milestones ===", flush=True)
    ess_values = []
    for strategy, symbol, direction, raw_n in by_symbol_dir[:12]:
        for milestone_r in (0.5, 1.0):
            fields = build_state_fingerprint(
                strategy=strategy, symbol=symbol, direction=direction, original_regime=None, current_regime="TREND",
                current_r=milestone_r, max_achieved_r=milestone_r, min_achieved_r=0.0, elapsed_seconds=1800,
                is_at_or_beyond_breakeven=milestone_r >= 0, is_trailing_action=False,
            )
            try:
                stats = adaptive_cache.cached_weighted_state_statistics(strategy=strategy, symbol=symbol, direction=direction, query_fields=fields)
                ess = float(stats.get("effective_sample_size") or 0)
            except Exception as exc:
                ess = -1.0
                print(f"lookup_failed {strategy} {symbol} {direction} r={milestone_r}: {exc.__class__.__name__}", flush=True)
                continue
            ess_values.append(ess)
            print(f"{strategy} {symbol} {direction} r={milestone_r}: ess={ess}", flush=True)

    if ess_values:
        print(f"\nESS summary: n_samples={len(ess_values)} median={pystats.median(ess_values):.2f} max={max(ess_values):.2f} min={min(ess_values):.2f}", flush=True)


if __name__ == "__main__":
    main()
