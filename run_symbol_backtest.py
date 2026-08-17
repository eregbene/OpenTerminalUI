"""Parameterized point-in-time Historical Intelligence backtest for any symbol (generalizes
run_eurusd_backtest.py's methodology, unchanged, to validate tiered-ESS activation evidence
across pairs -- see docs discussion 2026-08-17).

Usage: python run_symbol_backtest.py SYMBOL

Reuses the EXISTING, unmodified production pipeline end to end -- no new fingerprint/statistics/
verdict logic. See run_eurusd_backtest.py's module docstring for the full methodology
explanation (candidates from real bulk_replay fingerprints, similarity.similarity_statistics
with as_of=entry_time-purge for walk-forward safety, entry_intelligence's own scoring/threshold
logic, forward-only revealed outcomes).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import timedelta

from backend.historical_intelligence import entry_intelligence, similarity
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM
from backend.historical_intelligence.walk_forward import _PURGE_WINDOW
from backend.shared.db import SessionLocal

_OUTPUT_DIR = "/data/historical_intelligence"
SAMPLE_STRIDE = 15


def _dims(fp):
    return similarity._fingerprint_to_dims(fp)


def load_candidates(symbol: str) -> list[tuple]:
    with SessionLocal() as db:
        rows = (
            db.query(HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM)
            .join(HistoricalSetupOutcomeORM, HistoricalSetupOutcomeORM.fingerprint_id == HistoricalPatternFingerprintORM.fingerprint_id)
            .filter(
                HistoricalPatternFingerprintORM.canonical_symbol == symbol,
                HistoricalSetupOutcomeORM.resolution_status == "RESOLVED",
                HistoricalSetupOutcomeORM.data_quality != "UNTRUSTED",
            )
            .order_by(HistoricalPatternFingerprintORM.entry_time.asc())
            .all()
        )
    return rows


def evaluate_one(fp, outcome) -> dict:
    as_of = fp.entry_time - _PURGE_WINDOW
    sim_stats = similarity.similarity_statistics(
        canonical_symbol=fp.canonical_symbol, direction=fp.direction, anchor_strategy=fp.anchor_strategy,
        strategy_version=fp.strategy_version, query_dims=_dims(fp), regime_broad=fp.regime_broad,
        top_k=100, as_of=as_of,
    )
    normalized = entry_intelligence._normalize_similarity_stats(sim_stats)
    verdict_result = entry_intelligence._evaluate_from_stats(
        trust_state="BACKTEST", stats=normalized, peer_group_hash=fp.peer_group_hash,
        source="SIMILARITY_WEIGHTED_BACKTEST", evaluation_source="SIMILARITY_WEIGHTED",
    )
    return {
        "fingerprint_id": fp.fingerprint_id,
        "entry_time": fp.entry_time.isoformat(),
        "provider": fp.provider,
        "strategy": fp.anchor_strategy,
        "direction": fp.direction,
        "regime": fp.regime,
        "session": fp.session,
        "raw_neighbor_count": sim_stats.get("raw_neighbor_count", 0),
        "very_close_matches": sim_stats.get("very_close_matches", 0),
        "effective_sample_size": sim_stats.get("effective_sample_size", 0.0),
        "median_similarity": sim_stats.get("median_similarity"),
        "predicted_p_1r": sim_stats.get("weighted_probability_1r"),
        "predicted_expectancy_r": sim_stats.get("weighted_expectancy_r"),
        "historical_score": verdict_result.get("historical_score"),
        "historical_decision": verdict_result.get("historical_decision"),
        "status": verdict_result.get("status"),
        "actual_outcome_r": outcome.outcome_r,
        "actual_reached_1r": outcome.reached_1r,
        "actual_reached_2r": outcome.reached_2r,
        "actual_tp_hit": outcome.tp_hit,
        "actual_sl_hit": outcome.sl_hit,
        "actual_mfe_r": outcome.mfe_r,
        "actual_mae_r": outcome.mae_r,
        "actual_immediate_failure": outcome.immediate_failure,
    }


def main() -> None:
    symbol = sys.argv[1].upper()
    records_path = f"{_OUTPUT_DIR}/{symbol.lower()}_backtest_records.json"
    progress_path = f"{_OUTPUT_DIR}/{symbol.lower()}_backtest_progress.json"
    os.makedirs(_OUTPUT_DIR, exist_ok=True)

    all_rows = load_candidates(symbol)
    rows = all_rows[::SAMPLE_STRIDE] if SAMPLE_STRIDE > 1 else all_rows
    print(f"Loaded {len(all_rows)} resolved {symbol} candidates (any provider); sampling every {SAMPLE_STRIDE} chronologically -> {len(rows)} to evaluate", flush=True)
    if not rows:
        print("NO CANDIDATES AVAILABLE YET -- corpus not ready for this symbol.", flush=True)
        return

    records: list[dict] = []
    start_index = 0
    if os.path.exists(records_path):
        try:
            with open(records_path) as f:
                prior = json.load(f)
            if isinstance(prior, list) and prior and all(
                r.get("fingerprint_id") == rows[idx][0].fingerprint_id for idx, r in enumerate(prior)
            ):
                records = prior
                start_index = len(prior)
                print(f"Resuming from checkpoint: {start_index}/{len(rows)} already evaluated.", flush=True)
        except (json.JSONDecodeError, OSError, IndexError):
            print("Checkpoint file present but unusable -- starting fresh.", flush=True)

    for i, (fp, outcome) in enumerate(rows):
        if i < start_index:
            continue
        try:
            records.append(evaluate_one(fp, outcome))
        except Exception as exc:
            print(f"  [{i}] evaluation failed for {fp.fingerprint_id}: {exc.__class__.__name__}: {exc}", flush=True)
        if (i + 1) % 200 == 0:
            print(f"  ...evaluated {i + 1}/{len(rows)}", flush=True)
        if (i + 1) % 500 == 0 or (i + 1) == len(rows):
            tmp_path = f"{records_path}.tmp"
            with open(tmp_path, "w") as f:
                json.dump(records, f, indent=2, default=str)
            os.replace(tmp_path, records_path)
            with open(progress_path, "w") as f:
                json.dump({"evaluated": i + 1, "total": len(rows)}, f)
            print(f"  [checkpoint] {i + 1}/{len(rows)} written to {records_path}", flush=True)

    print(f"\nEvaluated {len(records)} candidates for {symbol}.", flush=True)
    print(f"Full per-candidate records written to {records_path}", flush=True)


if __name__ == "__main__":
    main()
