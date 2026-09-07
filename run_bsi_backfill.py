"""bsi historical validation backfill driver -- Part 10/11 of the bsi directive.

Replays every bsi subtype through the SAME canonical, point-in-time-safe replay pipeline
every other strategy family is validated through (backend.historical_intelligence.bulk_replay),
reusing bars_as_of/build_strategy_context/build_fingerprint/outcomes.label_outcome completely
unmodified -- exactly the same functions replay_symbol_history_fast itself calls internally, just
orchestrated directly here because bsi registers ONE strategy_id ("bsi", the
dispatcher) in EVALUATORS/STRATEGY_FAMILIES per the Part 8 architecture decision (one family, many
subtypes), while Part 10/11 need each of the 7 named subtypes' OWN independent outcome
distribution, plus the Part 11 component-attribution ladder -- neither is obtainable by walking
evaluate_all()'s registered-strategy loop alone. This script does NOT reimplement replay
mechanics, detection, fingerprinting, or outcome labeling -- it only widens WHICH evaluator
function gets called at each already-point-in-time-safe instant, and under WHICH strategy_id the
resulting fingerprint/outcome gets persisted (bsi__<subtype> / bsi__component_<stage>).

bsi's STRATEGY_FAMILIES entry defaults to DISABLED (module docstring, point 8) specifically
so the LIVE running DEMO backend never evaluates it. This script reaches the real evaluators
anyway by setting MT5_STRATEGY_ACTIVATION_BSI=SHADOW_MT5 as a PROCESS-LOCAL environment
variable for this script's own process only -- never written to the deployed container's
.env/docker-compose. Replay only ever calls evaluate_*/build_candidates, never execution.py, so
this has zero live-trading impact regardless of activation status.

Persists into HistoricalPatternFingerprintORM/HistoricalSetupOutcomeORM -- the SAME research
tables every prior strategy validation in this codebase writes to (wyckoff, donchian_trend_
follow, session_liquidity_breakout, the mean_reversion/trend_pullback evidence campaign) -- NOT
any live trading table (mt5_scheduler_candidates, mt5_trade_records, mt5_order_records,
adaptive_position_states are never touched, imported, or written to by this script or by anything
it calls).

Usage:
    python run_bsi_backfill.py --symbols EURUSD GBPUSD --start 2018-01-01 --end 2026-08-01
    python run_bsi_backfill.py --pilot --symbol EURUSD --days 30   # throughput measurement only
    python run_bsi_backfill.py --subtypes order_flow abc          # scope to specific subtypes
    python run_bsi_backfill.py --components                       # Part 11 ladder instead of subtypes

Requires a reachable historical candle corpus (mt5_canonical_candles / mt5_candle_revisions) --
point this at the real multi-year Postgres corpus via DATABASE_URL, not the tiny local dev SQLite
fixture (see bsi_final_report.md's own "environment limitation" section for why this could
not be executed during the authoring session).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

os.environ.setdefault("MT5_STRATEGY_ACTIVATION_BSI", "SHADOW_MT5")

from backend.historical_intelligence import outcomes  # noqa: E402
from backend.historical_intelligence.fingerprint import build_fingerprint  # noqa: E402
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM  # noqa: E402
from backend.historical_intelligence.replay import (  # noqa: E402
    STRATEGY_REPLAY_VERSION,
    _MIN_BARS,
    _ReplayQuote,
    bars_as_of,
    build_strategy_context,
    required_lookback,
)
from backend.market_structure.engine import analyze_bars  # noqa: E402
from backend.mt5_strategies.families.bsi_engine import (  # noqa: E402
    COMPONENT_STAGES,
    evaluate_bsi_component_stage,
    evaluate_bsi_subtype,
)
from backend.shared.db import SessionLocal  # noqa: E402

_ALL_SUBTYPES = ("bsi_order_flow", "bsi_abc", "bsi_asian", "bsi_new_york", "bsi_under_over", "bsi_0930", "bsi_abcd", "bsi_reactionary", "bsi_ob_liquidity")
_SYMBOL_BROKER_MAP = {s: s for s in ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURJPY", "GBPJPY", "XAUUSD"]}
_STRIDE_DEFAULT = timedelta(minutes=15)
_COMMIT_BATCH_SIZE = 25


def _fingerprint_id(canonical_symbol: str, at: datetime, pseudo_strategy_id: str) -> str:
    return "HPF_" + hashlib.sha256(f"BSI:{canonical_symbol}:{at.isoformat()}:{pseudo_strategy_id}".encode()).hexdigest()[:40]


def _persist(db: Any, *, canonical_symbol: str, broker_symbol: str, at: datetime, pseudo_strategy_id: str, ctx: Any, signal: Any, provider: str) -> bool:
    if not signal.valid or signal.proposed_entry is None or signal.stop_loss is None or signal.take_profit is None:
        return False
    fields = build_fingerprint(
        ctx=ctx, strategy_id=pseudo_strategy_id, contributing_strategies=[pseudo_strategy_id], strategy_family="bsi",
        strategy_version=STRATEGY_REPLAY_VERSION, source_quality_tier="RECONSTRUCTED", provider=provider, proxy=False,
        entry=float(signal.proposed_entry), stop_loss=float(signal.stop_loss), take_profit=float(signal.take_profit),
        entry_time=at, confidence_band=None, real_spread=None,
    )
    fingerprint_id = _fingerprint_id(canonical_symbol, at, pseudo_strategy_id)
    row = db.get(HistoricalPatternFingerprintORM, fingerprint_id)
    already_existed = row is not None
    if row is None:
        row = HistoricalPatternFingerprintORM(fingerprint_id=fingerprint_id, source_evaluation_id=None, created_at=datetime.now(timezone.utc))
        db.add(row)
    for key, value in fields.items():
        setattr(row, key, value)
    if not already_existed:
        outcomes.label_outcome(
            fingerprint_id=fingerprint_id, canonical_symbol=canonical_symbol, broker_symbol=broker_symbol,
            direction=fields["direction"], entry=fields["entry"], stop_loss=fields["stop_loss"], take_profit=fields["take_profit"],
            entry_time=fields["entry_time"], provider=provider, real_spread=None, db=db,
        )
    return not already_existed


def _checkpoint(canonical_symbol: str, pseudo_strategy_ids: list[str], window_start: datetime, window_end: datetime) -> datetime | None:
    with SessionLocal() as db:
        latest = (
            db.query(HistoricalPatternFingerprintORM.entry_time)
            .filter(
                HistoricalPatternFingerprintORM.canonical_symbol == canonical_symbol,
                HistoricalPatternFingerprintORM.anchor_strategy.in_(pseudo_strategy_ids),
                HistoricalPatternFingerprintORM.entry_time >= window_start,
                HistoricalPatternFingerprintORM.entry_time < window_end,
            )
            .order_by(HistoricalPatternFingerprintORM.entry_time.desc())
            .first()
        )
    return latest[0] if latest else None


async def run_one(*, canonical_symbol: str, broker_symbol: str, start: datetime, end: datetime, pseudo_ids: list[str],
                   mode: str, provider: str = "MT5", stride: timedelta = _STRIDE_DEFAULT, resume: bool = True, progress_every: int = 500) -> dict:
    """mode: 'subtypes' calls evaluate_bsi_subtype for each id in pseudo_ids (bare subtype
    names); 'components' calls evaluate_bsi_component_stage for each id in pseudo_ids
    (COMPONENT_STAGES names). Persisted strategy_id is always prefixed 'bsi__<id>' so it
    never collides with the single registered 'bsi' dispatcher's own live-path fingerprints."""
    at = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    end = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
    persisted_ids = [f"bsi__{pid}" for pid in pseudo_ids]

    resumed_from = None
    if resume:
        checkpoint = _checkpoint(canonical_symbol, persisted_ids, at, end)
        if checkpoint is not None:
            checkpoint = checkpoint if checkpoint.tzinfo else checkpoint.replace(tzinfo=timezone.utc)
            if checkpoint + stride > at:
                at = checkpoint + stride
                resumed_from = checkpoint.isoformat()

    instants_walked = insufficient_history = occurrences_new = occurrences_existing = errors = 0
    t0 = time.perf_counter()
    h1_cache: tuple[datetime, Any] | None = None
    h4_cache: tuple[datetime, Any] | None = None

    db = SessionLocal()
    pending = 0
    try:
        while at < end:
            instants_walked += 1
            try:
                m15 = await bars_as_of(canonical_symbol=canonical_symbol, broker_symbol=broker_symbol, timeframe="M15", at=at, count=required_lookback(timeframe="M15"), provider=provider)
                h1 = await bars_as_of(canonical_symbol=canonical_symbol, broker_symbol=broker_symbol, timeframe="H1", at=at, count=required_lookback(timeframe="H1"), provider=provider)
                h4 = await bars_as_of(canonical_symbol=canonical_symbol, broker_symbol=broker_symbol, timeframe="H4", at=at, count=required_lookback(timeframe="H4"), provider=provider)
            except Exception:
                errors += 1
                at += stride
                continue

            if len(m15) < _MIN_BARS["M15"] or len(h1) < _MIN_BARS["H1"] or len(h4) < _MIN_BARS["H4"]:
                insufficient_history += 1
                at += stride
                continue

            last_close = m15[-1].close
            quote = _ReplayQuote(bid=last_close, ask=last_close, spread=__import__("decimal").Decimal("0"))
            m15_rows = [c.model_dump(mode="json") for c in m15]
            h1_rows = [c.model_dump(mode="json") for c in h1]
            h4_rows = [c.model_dump(mode="json") for c in h4]

            try:
                h1_last_time = h1[-1].time
                if h1_cache is not None and h1_cache[0] == h1_last_time:
                    h1_snapshot = h1_cache[1]
                else:
                    h1_snapshot = analyze_bars(h1_rows, symbol=broker_symbol, timeframe="H1")
                    h1_cache = (h1_last_time, h1_snapshot)
                h4_last_time = h4[-1].time
                if h4_cache is not None and h4_cache[0] == h4_last_time:
                    h4_snapshot = h4_cache[1]
                else:
                    h4_snapshot = analyze_bars(h4_rows, symbol=broker_symbol, timeframe="H4")
                    h4_cache = (h4_last_time, h4_snapshot)

                ctx = build_strategy_context(
                    symbol=canonical_symbol, broker_symbol=broker_symbol,
                    m15_rows=m15_rows, h1_rows=h1_rows, h4_rows=h4_rows,
                    bid=quote.bid, ask=quote.ask, spread=quote.spread,
                    now=at, h1_snapshot=h1_snapshot, h4_snapshot=h4_snapshot,
                )
            except Exception:
                errors += 1
                at += stride
                continue

            if ctx is None:
                insufficient_history += 1
                at += stride
                continue

            for pid in pseudo_ids:
                try:
                    signal = evaluate_bsi_subtype(ctx, pid) if mode == "subtypes" else evaluate_bsi_component_stage(ctx, pid)
                    is_new = _persist(db, canonical_symbol=canonical_symbol, broker_symbol=broker_symbol, at=at,
                                       pseudo_strategy_id=f"bsi__{pid}", ctx=ctx, signal=signal, provider=provider)
                    if signal.valid:
                        if is_new:
                            occurrences_new += 1
                            pending += 1
                        else:
                            occurrences_existing += 1
                except Exception:
                    errors += 1
                    db.rollback()

            if pending >= _COMMIT_BATCH_SIZE:
                try:
                    db.commit()
                except Exception as exc:
                    # 2026-08-30: a real bug (execution_costs.COMMISSION_UNKNOWN one char over its
                    # own String(24) column -- now fixed) crashed this entire function on the first
                    # bad row inside a batch, since SQLAlchemy defers the actual INSERT to flush/
                    # commit time -- past the per-pid try/except above. Overnight backtest runs
                    # must survive one bad row without losing every symbol queued after it (Part
                    # 24: "diagnose and continue"); this trades a bounded, logged loss (this one
                    # batch's occurrences) for the run continuing to completion and checkpointing
                    # normally from here.
                    errors += 1
                    print(f"  {canonical_symbol} COMMIT_FAILED (batch of <= {_COMMIT_BATCH_SIZE} occurrences lost, continuing): {type(exc).__name__}: {exc}", flush=True)
                    db.rollback()
                pending = 0
            if progress_every and instants_walked % progress_every == 0:
                elapsed = time.perf_counter() - t0
                print(f"  {canonical_symbol} walked={instants_walked} new={occurrences_new} existing={occurrences_existing} "
                      f"insufficient={insufficient_history} errors={errors} elapsed={elapsed:.1f}s", flush=True)
            at += stride
        try:
            db.commit()
        except Exception as exc:
            errors += 1
            print(f"  {canonical_symbol} FINAL_COMMIT_FAILED: {type(exc).__name__}: {exc}", flush=True)
            db.rollback()
    finally:
        db.close()

    elapsed = time.perf_counter() - t0
    return {
        "canonical_symbol": canonical_symbol, "mode": mode, "pseudo_ids": pseudo_ids,
        "start": start.isoformat(), "end": end.isoformat(), "resumed_from": resumed_from,
        "instants_walked": instants_walked, "insufficient_history": insufficient_history,
        "occurrences_new": occurrences_new, "occurrences_existing": occurrences_existing, "errors": errors,
        "elapsed_seconds": round(elapsed, 2),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*", default=["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "GBPJPY"])
    parser.add_argument("--start", type=str, default=None)
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--symbol", type=str, default="EURUSD")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--subtypes", nargs="*", default=list(_ALL_SUBTYPES))
    parser.add_argument("--components", action="store_true", help="run the Part 11 component-attribution ladder instead of the named subtypes")
    args = parser.parse_args()

    assert os.getenv("MT5_STRATEGY_ACTIVATION_BSI") == "SHADOW_MT5", "refusing to run: bsi activation override missing"

    mode = "components" if args.components else "subtypes"
    pseudo_ids = list(COMPONENT_STAGES) if args.components else args.subtypes

    if args.pilot:
        end = datetime(2026, 8, 1, tzinfo=timezone.utc)
        start = end - timedelta(days=args.days)
        broker_symbol = _SYMBOL_BROKER_MAP.get(args.symbol.upper(), args.symbol.upper())
        print(f"PILOT ({mode}): {args.symbol} {start.isoformat()} -> {end.isoformat()} ids={pseudo_ids}", flush=True)
        result = await run_one(canonical_symbol=args.symbol.upper(), broker_symbol=broker_symbol, start=start, end=end, pseudo_ids=pseudo_ids, mode=mode)
        print(result, flush=True)
        return

    end = datetime.fromisoformat(args.end) if args.end else datetime(2026, 8, 1, tzinfo=timezone.utc)
    start = datetime.fromisoformat(args.start) if args.start else end - timedelta(days=8 * 365)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)

    print(f"Backfilling bsi ({mode}={pseudo_ids}) for {args.symbols} over {start.isoformat()} -> {end.isoformat()}", flush=True)
    for symbol in args.symbols:
        broker_symbol = _SYMBOL_BROKER_MAP.get(symbol.upper(), symbol.upper())
        print(f"\n=== {symbol} ===", flush=True)
        try:
            result = await run_one(canonical_symbol=symbol.upper(), broker_symbol=broker_symbol, start=start, end=end, pseudo_ids=pseudo_ids, mode=mode)
            print(result, flush=True)
        except Exception as exc:
            # Part 24: one symbol's unexpected failure must never stop the rest of an overnight
            # multi-symbol run -- log it plainly and move on; already-committed batches for this
            # symbol are safe and resume() will pick back up from its own last checkpoint later.
            print(f"  {symbol} SYMBOL_RUN_FAILED, continuing with remaining symbols: {type(exc).__name__}: {exc}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
