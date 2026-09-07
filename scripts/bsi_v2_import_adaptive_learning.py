from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text

from backend.adaptive_management.service import detect_regime
from backend.historical_intelligence import outcomes
from backend.historical_intelligence.adaptive_backfill import backfill_trade
from backend.historical_intelligence.fingerprint import build_fingerprint
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM
from backend.market_structure.bar_utils import average_true_range, normalize_bars
from backend.market_structure.engine import analyze_bars
from backend.mt5_strategies.context import REGIME_NEUTRAL, StrategyContext
from backend.mt5_strategies.families.bsi_v2_scaffold import BSI_BASELINE_V2_AUDIOVISUAL
from backend.shared.db import SessionLocal


RUN_ID = "bsi_v2_august_video_recheck"
ANCHOR_STRATEGY = "bsi"


def _ensure_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _parse_time(value: str) -> datetime:
    return _ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _fingerprint_id(trade: dict[str, Any]) -> str:
    payload = "|".join(
        [
            RUN_ID,
            trade["symbol"].upper(),
            trade["subtype"],
            trade["at"],
            trade["direction"].upper(),
            str(trade["entry"]),
            str(trade["stop_loss"]),
            str(trade["take_profit"]),
        ]
    )
    return "HPF_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


def _row_time(row: Any) -> datetime:
    return _ensure_utc(row["timestamp_utc"] or row["timestamp"])


def _row_dict(row: Any) -> dict[str, Any]:
    return {
        "symbol": row["canonical_symbol"],
        "timeframe": row["timeframe"],
        "time": _row_time(row).isoformat(),
        "open": str(row["open"]),
        "high": str(row["high"]),
        "low": str(row["low"]),
        "close": str(row["close"]),
        "tick_volume": int(row["tick_volume"] or 0),
        "spread": int(row["spread"] or 0),
        "real_volume": int(row["real_volume"] or 0),
        "complete": True,
        "source": row["provider"],
    }


def _load(symbol: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        rows = list(
            db.execute(
                text(
                    """
                    select provider, canonical_symbol, timeframe, timestamp, timestamp_utc,
                           open, high, low, close, tick_volume, spread, real_volume
                    from mt5_canonical_candles
                    where provider = 'MT5'
                      and canonical_symbol = :symbol
                      and timeframe = 'M15'
                      and timestamp >= :start_ts
                      and timestamp < :end_ts
                      and quality != 'INVALID'
                    order by timestamp asc, candle_id asc
                    """
                ),
                {"symbol": symbol.upper(), "start_ts": start, "end_ts": end},
            ).mappings()
        )
    by_time = {_row_time(row): _row_dict(row) for row in rows}
    return [by_time[k] for k in sorted(by_time)]


def _times(rows: list[dict[str, Any]]) -> list[datetime]:
    return [datetime.fromisoformat(str(row["time"])) for row in rows]


def _slice_asof(rows: list[dict[str, Any]], times: list[datetime], at: datetime, count: int) -> list[dict[str, Any]]:
    idx = bisect_right(times, at)
    return rows[max(0, idx - count) : idx]


def _context_for_trade(symbol: str, at: datetime, rows: list[dict[str, Any]], times: list[datetime]) -> StrategyContext | None:
    m15_ctx = _slice_asof(rows, times, at, 100)
    if len(m15_ctx) < 60:
        return None
    snapshot = analyze_bars(m15_ctx, symbol=symbol, timeframe="M15")
    bars = normalize_bars(m15_ctx, symbol=symbol, timeframe="M15")
    atrs = average_true_range(bars, 14)
    regime_info = detect_regime(m15_ctx)
    last_close = Decimal(str(m15_ctx[-1]["close"]))
    return StrategyContext(
        account_id="historical_adaptive_learning",
        symbol=symbol,
        broker_symbol=symbol,
        generated_at=at,
        regime=str(regime_info.get("regime") or "unknown"),
        regime_confidence=float(regime_info.get("confidence") or 0.0),
        htf_trend_h4="unknown",
        htf_trend_h1="unknown",
        m15_snapshot=snapshot,
        m15_rows=m15_ctx,
        h1_rows=[],
        h4_rows=[],
        atr_m15=atrs[-1] if atrs else None,
        bid=last_close,
        ask=last_close,
        spread=Decimal("0"),
        market_regime=REGIME_NEUTRAL,
    )


async def import_file(
    path: Path,
    *,
    offset: int,
    limit: int | None,
    backfill: bool,
    compute_structure: bool,
    dry_run: bool,
    progress_every: int,
) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    trades = list(payload.get("trades") or [])
    if offset:
        trades = trades[offset:]
    if limit is not None:
        trades = trades[:limit]

    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        by_symbol.setdefault(str(trade["symbol"]).upper(), []).append(trade)

    imported = existing = skipped = outcomes_written = states_written = errors = processed = 0
    per_symbol: dict[str, dict[str, int]] = {}

    for symbol, symbol_trades in sorted(by_symbol.items()):
        start = min(_parse_time(t["at"]) for t in symbol_trades) - timedelta(days=7)
        end = max(_parse_time(t["at"]) for t in symbol_trades) + timedelta(days=10)
        rows = _load(symbol, start, end)
        times = _times(rows)
        per_symbol[symbol] = {"trades": len(symbol_trades), "imported": 0, "existing": 0, "states_written": 0, "skipped": 0}

        db = SessionLocal()
        try:
            for trade in symbol_trades:
                processed += 1
                try:
                    at = _parse_time(trade["at"])
                    ctx = _context_for_trade(symbol, at, rows, times)
                    if ctx is None:
                        skipped += 1
                        per_symbol[symbol]["skipped"] += 1
                        continue
                    fp_id = _fingerprint_id(trade)
                    row = db.get(HistoricalPatternFingerprintORM, fp_id)
                    was_existing = row is not None
                    if row is None:
                        row = HistoricalPatternFingerprintORM(fingerprint_id=fp_id)
                        db.add(row)

                    fields = build_fingerprint(
                        ctx=ctx,
                        strategy_id=ANCHOR_STRATEGY,
                        contributing_strategies=[ANCHOR_STRATEGY, str(trade["subtype"])],
                        strategy_family="bsi",
                        strategy_version=BSI_BASELINE_V2_AUDIOVISUAL,
                        source_quality_tier="RECONSTRUCTED",
                        provider="MT5",
                        proxy=False,
                        entry=float(trade["entry"]),
                        stop_loss=float(trade["stop_loss"]),
                        take_profit=float(trade["take_profit"]),
                        entry_time=at,
                        confidence_band=None,
                        economic_event_context={"source": RUN_ID, "bsi_v2_subtype": trade["subtype"]},
                    )
                    for key, value in fields.items():
                        setattr(row, key, value)
                    row.replay_run_id = RUN_ID
                    row.source_evaluation_id = f"{RUN_ID}:{trade['subtype']}"

                    if dry_run:
                        db.rollback()
                        imported += 0 if was_existing else 1
                        existing += 1 if was_existing else 0
                        continue

                    db.flush()
                    outcome_result = outcomes.label_outcome(
                        fingerprint_id=fp_id,
                        canonical_symbol=symbol,
                        broker_symbol=symbol,
                        direction=str(trade["direction"]).upper(),
                        entry=float(trade["entry"]),
                        stop_loss=float(trade["stop_loss"]),
                        take_profit=float(trade["take_profit"]),
                        entry_time=at,
                        provider="MT5",
                        db=db,
                    )
                    if outcome_result.get("resolution_status") == "RESOLVED":
                        outcomes_written += 1
                    if was_existing:
                        existing += 1
                        per_symbol[symbol]["existing"] += 1
                    else:
                        imported += 1
                        per_symbol[symbol]["imported"] += 1

                    if backfill:
                        db.flush()
                        outcome_row = db.query(HistoricalSetupOutcomeORM).filter(HistoricalSetupOutcomeORM.fingerprint_id == fp_id).first()
                        if outcome_row is not None and outcome_row.resolution_status == "RESOLVED":
                            written = await backfill_trade(row, outcome_row, db=db, compute_structure=compute_structure)
                            states_written += written
                            per_symbol[symbol]["states_written"] += written

                    if (imported + existing) % 25 == 0:
                        db.commit()
                    if progress_every and processed % progress_every == 0:
                        print(
                            json.dumps(
                                {
                                    "progress": processed,
                                    "trades_seen": len(trades),
                                    "symbol": symbol,
                                    "fingerprints_imported": imported,
                                    "fingerprints_existing": existing,
                                    "adaptive_states_written": states_written,
                                    "errors": errors,
                                },
                                sort_keys=True,
                            ),
                            flush=True,
                        )
                except Exception:
                    db.rollback()
                    errors += 1
            db.commit()
        finally:
            db.close()

    return {
        "run_id": RUN_ID,
        "source_file": str(path),
        "offset": offset,
        "trades_seen": len(trades),
        "trades_processed": processed,
        "fingerprints_imported": imported,
        "fingerprints_existing": existing,
        "outcomes_labeled": outcomes_written,
        "adaptive_states_written": states_written,
        "skipped": skipped,
        "errors": errors,
        "backfill_enabled": backfill,
        "compute_structure": compute_structure,
        "dry_run": dry_run,
        "per_symbol": per_symbol,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/research/bsi_v2_august_rerun_after_video_recheck_all.json")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--compute-structure", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--progress-every", type=int, default=50)
    args = parser.parse_args()

    result = asyncio.run(
        import_file(
            Path(args.input),
            offset=args.offset,
            limit=args.limit,
            backfill=args.backfill,
            compute_structure=args.compute_structure,
            dry_run=args.dry_run,
            progress_every=args.progress_every,
        )
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
