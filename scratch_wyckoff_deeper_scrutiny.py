"""Follow-up scrutiny after scratch_wyckoff_validation_report.py's first pass: (1) fix the
comparison-loader bug that crashed before printing existing-strategy comparison rows, (2) check
whether spring_sos_lps's edge holds up per-symbol or is a USDJPY-only artifact, (3) sanity-check
the implausibly high avg_winner_r/profit_factor for spring_sos_lps against real R-multiple/
stop-target distributions, (4) rerun pooled stats excluding USDJPY (the dominant outlier) and
separately excluding GBPJPY/XAUUSD (the H4-data-gap-affected symbols) to see whether any edge
survives without them."""
from __future__ import annotations

import os
import statistics as pystats
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

os.environ.setdefault("MT5_STRATEGY_ACTIVATION_WYCKOFF", "SHADOW_MT5")

from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM  # noqa: E402
from backend.shared.db import SessionLocal  # noqa: E402
from scratch_wyckoff_validation_report import (  # noqa: E402
    COMPARISON_STRATEGIES, SYMBOLS, WINDOW_END, WINDOW_START, _report_group, enrich, load_wyckoff_rows,
)

_ALL_STRATEGIES = COMPARISON_STRATEGIES + ["ema_trend", "trend_pullback", "breakout", "mean_reversion", "momentum", "session_breakout", "vwap_reversion", "mtfai1"]


def load_comparison_rows_fixed(strategy_id: str) -> list[dict]:
    with SessionLocal() as db:
        rows = (
            db.query(HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM)
            .join(HistoricalSetupOutcomeORM, HistoricalSetupOutcomeORM.fingerprint_id == HistoricalPatternFingerprintORM.fingerprint_id)
            .filter(HistoricalPatternFingerprintORM.anchor_strategy == strategy_id)
            .filter(HistoricalPatternFingerprintORM.canonical_symbol.in_(SYMBOLS))
            .filter(HistoricalPatternFingerprintORM.entry_time >= WINDOW_START, HistoricalPatternFingerprintORM.entry_time < WINDOW_END)
            .filter(HistoricalSetupOutcomeORM.resolution_status == "RESOLVED")
            .filter(HistoricalSetupOutcomeORM.data_quality != "UNTRUSTED")
            .all()
        )
    return [{
        "outcome_r": oc.outcome_r, "mfe_r": oc.mfe_r, "mae_r": oc.mae_r, "entry_time": fp.entry_time,
        "reached_1r": oc.reached_1r, "reached_2r": oc.reached_2r,
    } for fp, oc in rows]


_CACHE_PATH = "/data/research/wyckoff_backfill/enriched_rows_cache.json"


async def main() -> None:
    import json

    if os.path.exists(_CACHE_PATH):
        print(f"Loading cached enriched rows from {_CACHE_PATH}...", flush=True)
        with open(_CACHE_PATH) as f:
            rows = json.load(f)
        for r in rows:
            r["entry_time"] = datetime.fromisoformat(r["entry_time"])
    else:
        print("Reloading + re-enriching wyckoff rows (same as first pass)...", flush=True)
        rows = load_wyckoff_rows()
        rows = await enrich(rows)
        with open(_CACHE_PATH, "w") as f:
            json.dump([{**r, "entry_time": r["entry_time"].isoformat()} for r in rows], f)
        print(f"Cached {len(rows)} enriched rows to {_CACHE_PATH} for future reuse.", flush=True)
    print(f"n={len(rows)}", flush=True)

    print("\n" + "=" * 100)
    print("FIX 1: SAME-WINDOW COMPARISON AGAINST EXISTING STRATEGIES (bug fixed)")
    print("=" * 100)
    print(f"  wyckoff: {_report_group(rows)}")
    for strategy_id in _ALL_STRATEGIES:
        comp_rows = load_comparison_rows_fixed(strategy_id)
        if comp_rows:
            print(f"  {strategy_id}: {_report_group(comp_rows)}")
        else:
            print(f"  {strategy_id}: NO_IN_WINDOW_COVERAGE")

    print("\n" + "=" * 100)
    print("SCRUTINY 1: spring_sos_lps broken down BY SYMBOL (is the +4.64R edge USDJPY-only?)")
    print("=" * 100)
    spring_rows = [r for r in rows if r.get("setup") == "spring_sos_lps"]
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for r in spring_rows:
        by_symbol[r["symbol"]].append(r)
    for symbol, symbol_rows in sorted(by_symbol.items()):
        print(f"  {symbol}: {_report_group(symbol_rows)}")

    print("\n" + "=" * 100)
    print("SCRUTINY 2: phase_d_continuation broken down BY SYMBOL")
    print("=" * 100)
    phase_d_rows = [r for r in rows if r.get("setup") == "phase_d_continuation"]
    by_symbol2: dict[str, list[dict]] = defaultdict(list)
    for r in phase_d_rows:
        by_symbol2[r["symbol"]].append(r)
    for symbol, symbol_rows in sorted(by_symbol2.items()):
        print(f"  {symbol}: {_report_group(symbol_rows)}")

    print("\n" + "=" * 100)
    print("SCRUTINY 3: R-multiple / stop-target realism check for spring_sos_lps")
    print("=" * 100)
    winners = [r["outcome_r"] for r in spring_rows if r["outcome_r"] is not None and r["outcome_r"] > 0]
    if winners:
        winners_sorted = sorted(winners)
        print(f"  n_winners={len(winners)}  min={winners_sorted[0]:.2f}  p25={winners_sorted[len(winners_sorted)//4]:.2f}  "
              f"median={pystats.median(winners_sorted):.2f}  p75={winners_sorted[3*len(winners_sorted)//4]:.2f}  max={winners_sorted[-1]:.2f}")
        top5 = winners_sorted[-5:]
        print(f"  top 5 winners (R): {top5}")
        huge = [w for w in winners if w >= 10.0]
        print(f"  winners with R >= 10.0: {len(huge)} / {len(winners)} ({round(100*len(huge)/len(winners),1)}%)")
        contribution_from_huge = sum(huge) / sum(winners) if winners else None
        print(f"  fraction of TOTAL winning R contributed by >=10R winners: {round(contribution_from_huge,3) if contribution_from_huge else None}")

    print("\n" + "=" * 100)
    print("SCRUTINY 4: pooled stats EXCLUDING USDJPY (does any edge survive without the outlier symbol?)")
    print("=" * 100)
    ex_usdjpy = [r for r in rows if r["symbol"] != "USDJPY"]
    print(f"  all setups, ex-USDJPY: {_report_group(ex_usdjpy)}")
    ex_usdjpy_spring = [r for r in ex_usdjpy if r.get("setup") == "spring_sos_lps"]
    print(f"  spring_sos_lps only, ex-USDJPY: {_report_group(ex_usdjpy_spring)}")
    ex_usdjpy_phased = [r for r in ex_usdjpy if r.get("setup") == "phase_d_continuation"]
    print(f"  phase_d_continuation only, ex-USDJPY: {_report_group(ex_usdjpy_phased)}")

    print("\n" + "=" * 100)
    print("SCRUTINY 5: pooled stats EXCLUDING GBPJPY/XAUUSD (the H4-data-gap-affected symbols)")
    print("=" * 100)
    clean_symbols = {"EURUSD", "GBPUSD", "USDJPY"}
    clean_rows = [r for r in rows if r["symbol"] in clean_symbols]
    print(f"  all setups, EURUSD+GBPUSD+USDJPY only: {_report_group(clean_rows)}")
    clean_spring = [r for r in clean_rows if r.get("setup") == "spring_sos_lps"]
    print(f"  spring_sos_lps only, EURUSD+GBPUSD+USDJPY only: {_report_group(clean_spring)}")

    print("\n" + "=" * 100)
    print("SCRUTINY 6: spring_sos_lps chronological train/OOS split (pooled across symbols)")
    print("=" * 100)
    spring_sorted = sorted([r for r in spring_rows if r["outcome_r"] is not None], key=lambda r: r["entry_time"])
    rs = [r["outcome_r"] for r in spring_sorted]
    cut = len(rs) // 2
    print(f"  train50: {_report_group(spring_sorted[:cut])}")
    print(f"  oos50:   {_report_group(spring_sorted[cut:])}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
