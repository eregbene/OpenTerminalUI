"""ForexSB / MT5 overlap validation (ForexSB integration directive, Part 8).

Ingests a SMALL, recent M15 window from ForexSB (dataset_policy=FOREXSB_VALIDATION_OVERLAP,
deliberately separate from the main FOREXSB_BACKFILL corpus-expansion ingestion, which never
touches this recent range since it adds no historical depth there -- Part 4/9) purely so
quality.compare_provider_overlap can compare it against Bensim's already-ingested real MT5 M15
candles for the same window, via the existing, unmodified ingestion.reconcile_providers().

Never treats "identical broker ticks" as the bar -- reports the real disagreement distribution
(CONSISTENT/DIVERGENT/INCOMPATIBLE) so a real, evidence-based trust decision can be made,
especially for XAUUSD (Dukascopy CFD gold vs whatever this MT5 demo broker quotes for XAUUSD).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from backend.historical_intelligence import ingestion
from backend.historical_intelligence.providers.forexsb_provider import ForexSBHistoricalProvider

# All 10 Forex/MT5 symbols with a ForexSB backfill (see run_forexsb_backfill.py's own SYMBOLS
# list). Originally only 3 (EURUSD, GBPJPY, XAUUSD) had overlap validation; extended to the
# remaining 7 so every symbol gets the same MT5-vs-ForexSB data-quality confidence. Re-running
# the original 3 is cheap/idempotent (ingestion.backfill only fetches gaps) and backfills their
# rows with the newer p95/missing-bar-rate/timestamp-alignment/OHLC-consistency metrics too.
SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURJPY", "GBPJPY", "XAUUSD"]
WINDOW_DAYS = 60


async def validate_symbol(provider: ForexSBHistoricalProvider, symbol: str) -> dict:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=WINDOW_DAYS)
    ingest_result = await ingestion.backfill(
        provider, canonical_symbol=symbol, broker_symbol=symbol, timeframe="M15",
        start=start, end=end, dataset_policy="FOREXSB_VALIDATION_OVERLAP",
    )
    report = ingestion.reconcile_providers(
        canonical_symbol=symbol, timeframe="M15", start=start, end=end,
        left_provider="MT5", right_provider="FOREXSB",
    )
    return {"symbol": symbol, "ingest": ingest_result, "overlap": report}


async def main() -> None:
    provider = ForexSBHistoricalProvider()
    for symbol in SYMBOLS:
        result = await validate_symbol(provider, symbol)
        print(f"=== {symbol} ===", flush=True)
        print(f"  ingest: bars_fetched={result['ingest'].get('bars_fetched')} bars_persisted={result['ingest'].get('bars_persisted')} error={result['ingest'].get('error')}", flush=True)
        print(f"  overlap: {result['overlap']}", flush=True)
        await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
