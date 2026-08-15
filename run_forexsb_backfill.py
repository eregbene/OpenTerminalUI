"""ForexSB deep-history backfill worker (ForexSB integration directive).

Ingests ForexSB-sourced M30 (full available depth, ~2010-present, no MT5 boundary since MT5 has
zero native M30 rows today) and M15 (only the gap strictly BEFORE each symbol's own real MT5 M15
corpus start, so it never duplicates history Bensim's MT5 corpus already covers) for all 10
Bensim FX/MT5 instruments, then derives H1/H4/D1 from the same M30 series across the full range
(backstopping H1/H4 context for bulk_replay's M15 fingerprint walk over the gap window).

Entirely via the existing, unmodified ingestion.backfill() gap-aware/idempotent/resumable
pipeline (backend/historical_intelligence/ingestion.py) -- the same path MT5/Yahoo backfills
already use. This worker adds no new persistence logic of its own; it only supplies the new
provider and the per-symbol date ranges.

One symbol failing must not invalidate the remaining nine (Part 9) -- each symbol's backfill is
wrapped in its own try/except so a real failure on one instrument is logged and skipped, never
aborting the run.
"""
from __future__ import annotations

import asyncio
import sys
import traceback
from datetime import datetime, timezone

from backend.historical_intelligence import ingestion
from backend.historical_intelligence.providers.forexsb_provider import ForexSBHistoricalProvider

# Pause between each timeframe/symbol step -- on top of ingestion.py's own per-chunk pacing
# (Part 13: this backfill must not materially delay the live M5 cycle). A real interference was
# measured during this integration's own first full-scale run (live total_cycle_ms 2-3x its
# established baseline while this worker ran unthrottled); this and the per-chunk commit/pause in
# ingestion.py together are the fix.
_INTER_STEP_PAUSE_SECONDS = 5

# Real MT5 M15 canonical-candle earliest timestamp per symbol (queried directly against
# mt5_canonical_candles, provider=MT5, 2026-08-14). ForexSB M15 is ingested only for
# [FOREXSB_WIDE_START, this) per symbol -- strictly the gap, never overlapping what Bensim's own
# MT5 corpus already has natively.
MT5_M15_START = {
    "EURUSD": datetime(2022, 8, 4, 22, 45, tzinfo=timezone.utc),
    "GBPUSD": datetime(2024, 8, 7, 11, 30, tzinfo=timezone.utc),
    "USDJPY": datetime(2024, 8, 7, 13, 15, tzinfo=timezone.utc),
    "AUDUSD": datetime(2024, 8, 7, 12, 45, tzinfo=timezone.utc),
    "USDCAD": datetime(2024, 8, 7, 13, 15, tzinfo=timezone.utc),
    "USDCHF": datetime(2024, 8, 7, 13, 30, tzinfo=timezone.utc),
    "NZDUSD": datetime(2024, 8, 7, 13, 15, tzinfo=timezone.utc),
    "EURJPY": datetime(2024, 8, 7, 12, 15, tzinfo=timezone.utc),
    "GBPJPY": datetime(2024, 8, 7, 13, 15, tzinfo=timezone.utc),
    "XAUUSD": datetime(2024, 6, 25, 18, 0, tzinfo=timezone.utc),
}

SYMBOLS = list(MT5_M15_START.keys())
# Wide enough to capture ForexSB's true earliest bar for every symbol (real earliest confirmed
# empirically at 2009-11/2010-07) without hardcoding a precise per-symbol date -- fetch_bars()
# filters to whatever's actually real in the source file, never fabricates missing history.
FOREXSB_WIDE_START = datetime(2005, 1, 1, tzinfo=timezone.utc)
NOW = datetime.now(timezone.utc)


async def backfill_symbol(provider: ForexSBHistoricalProvider, symbol: str) -> dict:
    results: dict[str, dict] = {}
    results["M30"] = await ingestion.backfill(
        provider, canonical_symbol=symbol, broker_symbol=symbol, timeframe="M30",
        start=FOREXSB_WIDE_START, end=NOW, dataset_policy="FOREXSB_BACKFILL",
    )
    await asyncio.sleep(_INTER_STEP_PAUSE_SECONDS)
    m15_end = MT5_M15_START[symbol]
    results["M15"] = await ingestion.backfill(
        provider, canonical_symbol=symbol, broker_symbol=symbol, timeframe="M15",
        start=FOREXSB_WIDE_START, end=m15_end, dataset_policy="FOREXSB_BACKFILL",
    )
    for tf in ("H1", "H4", "D1"):
        await asyncio.sleep(_INTER_STEP_PAUSE_SECONDS)
        results[tf] = await ingestion.backfill(
            provider, canonical_symbol=symbol, broker_symbol=symbol, timeframe=tf,
            start=FOREXSB_WIDE_START, end=NOW, dataset_policy="FOREXSB_BACKFILL",
        )
    return results


async def main(symbols: list[str]) -> None:
    provider = ForexSBHistoricalProvider()
    for i, symbol in enumerate(symbols):
        if i > 0:
            await asyncio.sleep(_INTER_STEP_PAUSE_SECONDS)
        provider._native_cache.clear()
        print(f"=== {symbol} ===", flush=True)
        try:
            results = await backfill_symbol(provider, symbol)
        except Exception:
            print(f"  {symbol}: FAILED (symbol skipped, continuing with remaining symbols)", flush=True)
            traceback.print_exc()
            continue
        for tf, r in results.items():
            print(f"  {symbol} {tf}: status={r.get('status')} bars_fetched={r.get('bars_fetched')} bars_persisted={r.get('bars_persisted')} suspect={r.get('bars_flagged_suspect')} invalid={r.get('bars_flagged_invalid')} error={r.get('error')}", flush=True)
    print("=== ALL SYMBOLS COMPLETE ===", flush=True)


if __name__ == "__main__":
    symbols = sys.argv[1:] or SYMBOLS
    asyncio.run(main(symbols))
