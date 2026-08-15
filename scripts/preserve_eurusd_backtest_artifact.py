#!/usr/bin/env python
"""Preserve the completed EURUSD Historical Intelligence backtest durably.

The backtest (run_eurusd_backtest.py) evaluated 4,542 real, already-replayed EURUSD
candidates and wrote its output to /app/eurusd_backtest_records.json -- inside the
backend container's writable layer, not volume-mounted, at real risk of loss on any
container recreation. Those files have already been copied to the mounted volume at
/data/research/raw_extracts/eurusd_backtest_2026-08-14/ (see docs/HISTORICAL_INTELLIGENCE_
PERSISTENCE_AUDIT.md for how that risk was found).

This script does NOT recompute the backtest -- it loads the already-computed records,
recomputes only the aggregate summary (via run_eurusd_backtest.py's own, exact report
functions -- not an approximation) so the summary is reproducible from the raw records,
attaches full provenance metadata, and persists both through
backend.historical_intelligence.provenance.preserve_research_result.

Run once: python scripts/preserve_eurusd_backtest_artifact.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.historical_intelligence import entry_intelligence, similarity  # noqa: E402
from backend.historical_intelligence import provenance  # noqa: E402
from backend.historical_intelligence.walk_forward import _PURGE_WINDOW  # noqa: E402
from run_eurusd_backtest import (  # noqa: E402
    ESS_THRESHOLDS,
    SAMPLE_STRIDE,
    SYMBOL,
    ess_threshold_analysis,
    mt5_only_vs_combined,
    verdict_breakdown,
)

RAW_EXTRACT_DIR = Path("/data/research/raw_extracts/eurusd_backtest_2026-08-14")

# The host git revision at the time these driver scripts were committed and this
# preservation step was run. get_git_revision_hash() (used as the default inside
# provenance.preserve_research_result) would return "dirty" when this script executes
# inside the container, since the Dockerfile does not copy .git -- so the real revision
# is supplied explicitly here, captured on the host checkout before this ran.
HOST_GIT_REVISION = "10f678e"


def main() -> None:
    records_path = RAW_EXTRACT_DIR / "eurusd_backtest_records.json"
    progress_path = RAW_EXTRACT_DIR / "eurusd_backtest_progress.json"

    with open(records_path) as f:
        records: list[dict] = json.load(f)
    with open(progress_path) as f:
        progress = json.load(f)

    if progress.get("evaluated") != len(records) or progress.get("total") != len(records):
        raise RuntimeError(
            f"Backtest appears incomplete or inconsistent: progress={progress}, "
            f"len(records)={len(records)} -- refusing to preserve a partial/mismatched result."
        )

    entry_times = [datetime.fromisoformat(r["entry_time"]) for r in records]
    strategy_set = sorted({r["strategy"] for r in records})
    providers = sorted({r["provider"] for r in records})

    summary = {
        "candidate_count": len(records),
        "ess_threshold_analysis": ess_threshold_analysis(records),
        "verdict_breakdown": verdict_breakdown(records),
        "mt5_only_vs_combined": mt5_only_vs_combined(records),
        "raw_neighbor_count_ge_100": sum(1 for r in records if r["raw_neighbor_count"] >= 100),
        "effective_sample_size_min": min(r["effective_sample_size"] for r in records),
        "effective_sample_size_median": sorted(r["effective_sample_size"] for r in records)[len(records) // 2],
        "effective_sample_size_max": max(r["effective_sample_size"] for r in records),
    }

    metadata = {
        "code_revision": HOST_GIT_REVISION,
        "code_revision_note": (
            "Captured from the host checkout at the time run_eurusd_backtest.py was committed "
            "and this preservation step was run. The backtest itself (2026-08-14 22:00 UTC) ran "
            "before that commit, while the driver script was still untracked -- so this revision "
            "identifies the code as re-committed/preserved, not a run-time-embedded stamp."
        ),
        "provider_set": providers,
        "candidate_count": len(records),
        "sample_stride": SAMPLE_STRIDE,
        "sample_stride_note": (
            "run_eurusd_backtest.py samples every Nth chronological candidate "
            "(SAMPLE_STRIDE=15) out of the full resolved EURUSD corpus -- 4,542 is a stride-15 "
            "subsample, not the full corpus."
        ),
        "date_range": {"start": min(entry_times).isoformat(), "end": max(entry_times).isoformat()},
        "top_k": similarity._DEFAULT_TOP_K,
        "top_k_note": (
            "run_eurusd_backtest.py passes top_k=100 as a literal, which matches "
            "similarity._DEFAULT_TOP_K today but is not imported from it. The live entry-decision "
            "path uses a smaller, separately-declared entry_intelligence._SIMILARITY_TOP_K="
            f"{entry_intelligence._SIMILARITY_TOP_K} for its live hot path -- this backtest used "
            "the module's own default, not the live value."
        ),
        "similarity_threshold": similarity._MIN_SIMILARITY_THRESHOLD,
        "ess_formula_version": similarity.SIMILARITY_MODEL_VERSION,
        "min_sample_for_live_influence": entry_intelligence._MIN_SAMPLE_FOR_LIVE_INFLUENCE,
        "strategy_set": strategy_set,
        "symbol": SYMBOL,
        "timeframe": "M5 entry signals over the full point-in-time corpus (multi-timeframe strategy context)",
        "walk_forward_semantics": (
            "Each candidate's evidence is restricted to analogs strictly earlier than "
            f"entry_time - purge_window (purge_window={_PURGE_WINDOW}, imported from "
            "walk_forward._PURGE_WINDOW). The script calls similarity.similarity_statistics/"
            "entry_intelligence directly and does not invoke walk_forward.run_walk_forward's own "
            "train/OOS split machinery -- only the purge-window constant is shared."
        ),
        "ess_thresholds_evaluated": ESS_THRESHOLDS,
        "source_files": {
            "driver": "run_eurusd_backtest.py",
            "raw_records": str(records_path),
            "raw_progress": str(progress_path),
        },
        "preserved_at": datetime.now(timezone.utc).isoformat(),
    }

    entry = provenance.preserve_research_result(
        bucket="backtests",
        run_id="eurusd_backtest_2026-08-14_4542",
        result_type="eurusd_backtest_4542",
        payload=records,
        summary=summary,
        metadata=metadata,
        git_revision=HOST_GIT_REVISION,
    )

    print(json.dumps({"run_id": entry["run_id"], "artifact_id": entry["artifact"]["artifact_id"],
                       "content_hash": entry["artifact"]["content_hash"], "summary": summary}, indent=2, default=str))


if __name__ == "__main__":
    main()
