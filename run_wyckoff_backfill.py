"""Wyckoff historical validation backfill driver.

Replays backend.mt5_strategies.families.evaluate_wyckoff through the SAME canonical,
point-in-time-safe replay pipeline every other strategy family is validated through
(backend.historical_intelligence.bulk_replay.replay_symbol_history_fast -- proven byte-identical
to the slow/reference replay path). No reimplementation of Wyckoff detection or of the replay
engine happens here; this script only drives it and reports the real counters it returns.

`strategy_ids=["wyckoff"]` scopes PERSISTENCE to wyckoff-only occurrences (evaluate_all still
genuinely runs every registered strategy each instant, per replay_symbol_history's own docstring
-- this only filters what gets written, not what gets evaluated).

Wyckoff's STRATEGY_FAMILIES entry defaults to DISABLED specifically so the LIVE running DEMO
backend never evaluates it (see models.py's own comment on that entry). Replay reaches the real
evaluator anyway by setting MT5_STRATEGY_ACTIVATION_WYCKOFF=SHADOW_MT5 as a PROCESS-LOCAL
environment variable for this script's own process only -- this is never written to the deployed
container's .env/docker-compose, and replay never mutates the broker regardless of activation
status (replay_at/replay_symbol_history only ever call evaluate_all/build_candidates, never
execution.py), so this is safe with zero live-trading impact.

Usage:
    python run_wyckoff_backfill.py --symbols EURUSD GBPUSD --start 2025-02-01 --end 2026-08-01
    python run_wyckoff_backfill.py --pilot --symbol EURUSD --days 14   # throughput measurement only
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("MT5_STRATEGY_ACTIVATION_WYCKOFF", "SHADOW_MT5")

from backend.historical_intelligence.bulk_replay import replay_symbol_history_fast  # noqa: E402

_SYMBOL_BROKER_MAP = {s: s for s in ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURJPY", "GBPJPY", "XAUUSD"]}


async def run_one(symbol: str, start: datetime, end: datetime) -> dict:
    """resume=False is DELIBERATE, not an oversight: bulk_replay.py's own _checkpoint() is scoped
    only by (canonical_symbol, window) -- it has no strategy filter. Every one of these 10
    symbols has already been extensively replayed for OTHER strategies by the long-running
    corpus workers, so resume=True would see "this window already has fingerprints" and skip
    straight to the end WITHOUT ever evaluating wyckoff for a single instant (confirmed directly:
    a resume=True pilot on EURUSD's most recent 14 days walked exactly 0 instants). Forcing a
    full walk here still costs nothing extra for the other strategies' already-persisted rows --
    strategy_ids=["wyckoff"] means only wyckoff occurrences are even considered for persistence,
    and persistence itself is idempotent per (symbol, instant, strategy) regardless."""
    broker_symbol = _SYMBOL_BROKER_MAP.get(symbol, symbol)
    result = await replay_symbol_history_fast(
        canonical_symbol=symbol, broker_symbol=broker_symbol, start=start, end=end,
        provider="MT5", strategy_ids=["wyckoff"], progress_every=1000, resume=False,
    )
    result["symbol"] = symbol
    return result


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*", default=["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "GBPJPY"])
    parser.add_argument("--start", type=str, default=None)
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--symbol", type=str, default="EURUSD")
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()

    assert os.getenv("MT5_STRATEGY_ACTIVATION_WYCKOFF") == "SHADOW_MT5", "refusing to run: wyckoff activation override missing"

    if args.pilot:
        end = datetime(2026, 8, 1, tzinfo=timezone.utc)
        start = end - timedelta(days=args.days)
        print(f"PILOT: {args.symbol} {start.isoformat()} -> {end.isoformat()}", flush=True)
        result = await run_one(args.symbol, start, end)
        print(result, flush=True)
        return

    end = datetime.fromisoformat(args.end) if args.end else datetime(2026, 8, 1, tzinfo=timezone.utc)
    start = datetime.fromisoformat(args.start) if args.start else end - timedelta(days=548)  # ~18 months
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)

    print(f"Backfilling wyckoff for {args.symbols} over {start.isoformat()} -> {end.isoformat()}", flush=True)
    for symbol in args.symbols:
        print(f"\n=== {symbol} ===", flush=True)
        result = await run_one(symbol, start, end)
        print(result, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
