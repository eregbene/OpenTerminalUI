"""Pipelined Historical Intelligence build-out from the ForexSB corpus (ForexSB integration
directive, continuation: pipeline stages rather than waiting for the full 10-symbol candle
backfill to finish).

Polls per-symbol readiness (real FOREXSB M15/H1/H4 coverage reaching each symbol's own gap-fill
target) and, as soon as a symbol is ready, immediately runs the EXISTING, unmodified,
already-provider-parameterized bulk_replay.replay_symbol_history_fast (provider=FOREXSB) to
generate entry fingerprints + resolve historical outcomes, then the EXISTING
adaptive_backfill.run_adaptive_backfill (now correctly scoped per-symbol after the checkpoint fix)
to build Adaptive Historical states from those fingerprints. No new fingerprint/outcome/adaptive
logic is written here -- this is purely an orchestration/trigger layer over what already exists.

Never touches replay-trust/parity/replay.py's MT5-only live-decision path. PostgreSQL remains the
only persistence target (same tables the live system already reads)."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from backend.brokers.mt5.orm import MT5CanonicalCandleORM
from backend.historical_intelligence import adaptive_backfill, bulk_replay
from backend.shared.db import SessionLocal

sys.path.insert(0, "/app")
from run_forexsb_backfill import MT5_M15_START, SYMBOLS  # noqa: E402

_POLL_SECONDS = 90
_MIN_BARS_REQUIRED = {"M15": 500, "H1": 100, "H4": 30}
_REPLAY_STRIDE = timedelta(minutes=15)


def _forexsb_coverage(symbol: str, timeframe: str) -> tuple[datetime, datetime, int] | None:
    with SessionLocal() as db:
        row = db.query(
            func.min(MT5CanonicalCandleORM.timestamp), func.max(MT5CanonicalCandleORM.timestamp), func.count()
        ).filter(
            MT5CanonicalCandleORM.provider == "FOREXSB",
            MT5CanonicalCandleORM.canonical_symbol == symbol,
            MT5CanonicalCandleORM.timeframe == timeframe,
        ).first()
    if row is None or row[0] is None:
        return None
    return row[0], row[1], row[2]


def _symbol_ready(symbol: str) -> tuple[datetime, datetime] | None:
    """Ready once M15/H1/H4 all have real FOREXSB coverage reaching this symbol's gap-fill
    target (MT5_M15_START[symbol]) with enough bars to be a genuinely usable window, and H1/H4
    context extends at least as far as the M15 data being replayed (bars_as_of needs both at
    every M15 instant)."""
    m15_end = MT5_M15_START[symbol]
    m15 = _forexsb_coverage(symbol, "M15")
    h1 = _forexsb_coverage(symbol, "H1")
    h4 = _forexsb_coverage(symbol, "H4")
    if not m15 or not h1 or not h4:
        return None
    if m15[2] < _MIN_BARS_REQUIRED["M15"] or h1[2] < _MIN_BARS_REQUIRED["H1"] or h4[2] < _MIN_BARS_REQUIRED["H4"]:
        return None
    m15_start, m15_max, _ = m15
    if m15_max < m15_end - timedelta(hours=6):
        return None  # M15 gap-fill hasn't reached its target end yet
    if h1[1] < m15_max or h4[1] < m15_max:
        return None  # H1/H4 derived context doesn't yet cover the full M15 range
    return m15_start, min(m15_max, m15_end)


async def process_symbol(symbol: str, start: datetime, end: datetime) -> dict:
    fp_result = await bulk_replay.replay_symbol_history_fast(
        canonical_symbol=symbol, broker_symbol=symbol, start=start, end=end,
        provider="FOREXSB", stride=_REPLAY_STRIDE,
    )
    adaptive_result = await adaptive_backfill.run_adaptive_backfill(canonical_symbol=symbol)
    return {"fingerprints": fp_result, "adaptive": adaptive_result}


async def main(symbols: list[str]) -> None:
    done: set[str] = set()
    while len(done) < len(symbols):
        progressed = False
        for symbol in symbols:
            if symbol in done:
                continue
            ready = _symbol_ready(symbol)
            if ready is None:
                continue
            start, end = ready
            print(f"=== {symbol} ready: FOREXSB M15 [{start.isoformat()} .. {end.isoformat()}) -- running bulk_replay + adaptive backfill ===", flush=True)
            try:
                result = await process_symbol(symbol, start, end)
                print(f"  {symbol} fingerprints: {result['fingerprints']}", flush=True)
                print(f"  {symbol} adaptive: {result['adaptive']}", flush=True)
            except Exception as exc:
                print(f"  {symbol}: FAILED ({exc.__class__.__name__}: {exc}) -- will not retry automatically, continuing with remaining symbols", flush=True)
            done.add(symbol)
            progressed = True
        if not progressed:
            print(f"[{datetime.now(timezone.utc).isoformat()}] waiting for more symbols to reach their gap-fill target... {len(done)}/{len(symbols)} done", flush=True)
            await asyncio.sleep(_POLL_SECONDS)
    print("ALL SYMBOLS PROCESSED", flush=True)


if __name__ == "__main__":
    symbols = sys.argv[1:] or SYMBOLS
    asyncio.run(main(symbols))
