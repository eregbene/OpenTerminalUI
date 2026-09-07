from __future__ import annotations

import json
import os
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text

os.environ.setdefault("MT5_PIVOT_TRENDLINE_COMPUTE_ENABLED", "false")

from backend.adaptive_management.service import detect_regime
from backend.market_structure.bar_utils import average_true_range, normalize_bars
from backend.market_structure.engine import analyze_bars
from backend.mt5_strategies.context import REGIME_NEUTRAL, StrategyContext
from backend.mt5_strategies.families.bsi_v2_engine import (
    BSI_V2_RESEARCH_EVALUATORS,
    evaluate_bsi_v2_subtype,
)
from backend.mt5_strategies.families.bsi_v2_lifecycle import BSILifecycleStore
from backend.shared.db import SessionLocal


DEFAULT_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURJPY", "GBPJPY", "XAUUSD"]
SYMBOLS = [s.strip().upper() for s in os.getenv("BSI_BACKTEST_SYMBOLS", ",".join(DEFAULT_SYMBOLS)).split(",") if s.strip()]
START = datetime(2026, 8, 1, tzinfo=timezone.utc)
END = datetime(2026, 9, 1, tzinfo=timezone.utc)
LOOKBACK_START = START - timedelta(days=120)
MAX_HOLD_BARS = 16  # 4 hours on M15, matching MT5_MAX_HOLDING_MINUTES=240.


@dataclass
class TradeOutcome:
    symbol: str
    subtype: str
    at: str
    direction: str
    entry: float
    stop_loss: float
    take_profit: float
    rr: float | None
    outcome: str
    r_multiple: float
    exit_at: str | None


def _ensure_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


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


def _on_timeframe_grid(ts: datetime, timeframe: str) -> bool:
    if ts.second or ts.microsecond:
        return False
    if timeframe == "M15":
        return ts.minute in {0, 15, 30, 45}
    if timeframe == "H1":
        return ts.minute == 0
    if timeframe == "H4":
        return ts.minute == 0 and ts.hour % 4 == 0
    return True


def _load(symbol: str, timeframe: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
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
                      and timeframe = :timeframe
                      and timestamp >= :start_ts
                      and timestamp < :end_ts
                      and quality != 'INVALID'
                    order by timestamp asc, candle_id asc
                    """
                ),
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "start_ts": start - timedelta(days=2),
                    "end_ts": end + timedelta(days=2),
                },
            ).mappings()
        )
    by_time: dict[datetime, dict[str, Any]] = {}
    for row in rows:
        ts = _row_time(row)
        if not _on_timeframe_grid(ts, timeframe):
            continue
        if start <= ts < end:
            by_time[ts] = _row_dict(row)
    return [by_time[k] for k in sorted(by_time)]


def _times(rows: list[dict[str, Any]]) -> list[datetime]:
    return [datetime.fromisoformat(str(r["time"])) for r in rows]


def _slice_asof(rows: list[dict[str, Any]], times: list[datetime], at: datetime, count: int) -> list[dict[str, Any]]:
    idx = bisect_right(times, at)
    return rows[max(0, idx - count) : idx]


def _simulate(signal: Any, future_rows: list[dict[str, Any]]) -> tuple[str, float, str | None]:
    entry = float(signal.proposed_entry)
    sl = float(signal.stop_loss)
    tp = float(signal.take_profit)
    risk = abs(entry - sl)
    if risk <= 0:
        return "bad_risk", 0.0, None
    direction = signal.direction.upper()
    for row in future_rows[:MAX_HOLD_BARS]:
        high = float(row["high"])
        low = float(row["low"])
        when = str(row["time"])
        if direction == "LONG":
            sl_hit = low <= sl
            tp_hit = high >= tp
        else:
            sl_hit = high >= sl
            tp_hit = low <= tp
        if sl_hit and tp_hit:
            return "sl_same_bar", -1.0, when
        if sl_hit:
            return "sl", -1.0, when
        if tp_hit:
            return "tp", abs(tp - entry) / risk, when
    return "open_timeout", 0.0, None


def main() -> None:
    all_trades: list[TradeOutcome] = []
    summary: dict[str, Any] = {
        "window": {"start": START.isoformat(), "end": END.isoformat(), "max_hold_m15_bars": MAX_HOLD_BARS},
        "symbols": {},
        "subtypes": {},
        "overall": {},
    }
    subtype_rejections: dict[str, Counter[str]] = defaultdict(Counter)
    symbol_rejections: dict[str, Counter[str]] = defaultdict(Counter)
    symbol_valid = Counter()
    subtype_valid = Counter()
    subtype_evaluated = Counter()
    symbol_evaluated = Counter()
    insufficient = Counter()
    lifecycle = {(symbol, subtype): BSILifecycleStore() for symbol in SYMBOLS for subtype in BSI_V2_RESEARCH_EVALUATORS}

    for symbol in SYMBOLS:
        print(f"LOAD {symbol}", flush=True)
        m15 = _load(symbol, "M15", LOOKBACK_START, END + timedelta(days=1))
        t15 = _times(m15)
        month_indices = [i for i, ts in enumerate(t15) if START <= ts < END]
        summary["symbols"][symbol] = {
            "m15_month_bars": len(month_indices),
            "h1_loaded": 0,
            "h4_loaded": 0,
            "valid_setups": 0,
            "rejections": {},
            "outcomes": {},
        }
        print(f"RUN {symbol} month_m15={len(month_indices)} loaded M15={len(m15)}", flush=True)
        for n, i in enumerate(month_indices, start=1):
            if n % 500 == 0:
                print(f"PROGRESS {symbol} {n}/{len(month_indices)} valid_so_far={summary['symbols'][symbol]['valid_setups']}", flush=True)
            at = t15[i]
            m15_ctx = _slice_asof(m15, t15, at, 100)
            if len(m15_ctx) < 60:
                insufficient[symbol] += 1
                continue
            last_close = Decimal(str(m15_ctx[-1]["close"]))
            snapshot = analyze_bars(m15_ctx, symbol=symbol, timeframe="M15")
            bars = normalize_bars(m15_ctx, symbol=symbol, timeframe="M15")
            atrs = average_true_range(bars, 14)
            regime_info = detect_regime(m15_ctx)
            ctx = StrategyContext(
                account_id="db_backtest",
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
            for subtype in BSI_V2_RESEARCH_EVALUATORS:
                subtype_evaluated[subtype] += 1
                symbol_evaluated[symbol] += 1
                signal = evaluate_bsi_v2_subtype(ctx, subtype, lifecycle=lifecycle[(symbol, subtype)])
                if not signal.valid:
                    reason = signal.rejection_reason or "UNKNOWN_REJECTION"
                    subtype_rejections[subtype][reason] += 1
                    symbol_rejections[symbol][reason] += 1
                    continue
                if signal.proposed_entry is None or signal.stop_loss is None or signal.take_profit is None:
                    subtype_rejections[subtype]["VALID_BUT_NOT_EXECUTABLE"] += 1
                    symbol_rejections[symbol]["VALID_BUT_NOT_EXECUTABLE"] += 1
                    continue
                future = m15[i + 1 : i + 1 + MAX_HOLD_BARS]
                outcome, r_mult, exit_at = _simulate(signal, future)
                trade = TradeOutcome(
                    symbol=symbol,
                    subtype=subtype,
                    at=at.isoformat(),
                    direction=signal.direction,
                    entry=float(signal.proposed_entry),
                    stop_loss=float(signal.stop_loss),
                    take_profit=float(signal.take_profit),
                    rr=float(signal.reward_risk) if signal.reward_risk is not None else None,
                    outcome=outcome,
                    r_multiple=round(r_mult, 4),
                    exit_at=exit_at,
                )
                all_trades.append(trade)
                symbol_valid[symbol] += 1
                subtype_valid[subtype] += 1
                summary["symbols"][symbol]["valid_setups"] += 1
                summary["symbols"][symbol]["outcomes"][outcome] = summary["symbols"][symbol]["outcomes"].get(outcome, 0) + 1
        print(f"DONE {symbol} valid={summary['symbols'][symbol]['valid_setups']}", flush=True)

    for symbol in SYMBOLS:
        summary["symbols"][symbol]["evaluated"] = symbol_evaluated[symbol]
        summary["symbols"][symbol]["insufficient_contexts"] = insufficient[symbol]
        summary["symbols"][symbol]["rejections"] = dict(symbol_rejections[symbol].most_common())
    for subtype in BSI_V2_RESEARCH_EVALUATORS:
        rows = [t for t in all_trades if t.subtype == subtype]
        outcomes = Counter(t.outcome for t in rows)
        summary["subtypes"][subtype] = {
            "evaluated": subtype_evaluated[subtype],
            "valid_setups": subtype_valid[subtype],
            "outcomes": dict(outcomes),
            "net_r": round(sum(t.r_multiple for t in rows), 4),
            "top_rejections": dict(subtype_rejections[subtype].most_common(10)),
        }
    outcome_counts = Counter(t.outcome for t in all_trades)
    closed = outcome_counts["tp"] + outcome_counts["sl"] + outcome_counts["sl_same_bar"]
    wins = outcome_counts["tp"]
    summary["overall"] = {
        "contexts": sum(symbol_evaluated.values()) // max(1, len(BSI_V2_RESEARCH_EVALUATORS)),
        "subtype_evaluations": sum(subtype_evaluated.values()),
        "valid_setups": len(all_trades),
        "outcomes": dict(outcome_counts),
        "closed_win_rate": round((wins / closed) * 100, 2) if closed else None,
        "net_r": round(sum(t.r_multiple for t in all_trades), 4),
    }
    result = {"summary": summary, "trades": [asdict(t) for t in all_trades]}
    suffix = "all" if SYMBOLS == DEFAULT_SYMBOLS else "_".join(SYMBOLS).lower()
    out = Path(os.getenv("BSI_BACKTEST_OUT", f"data/research/bsi_v2_last_month_db_backtest_2026_08_{suffix}.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"RESULT_PATH={out}")


if __name__ == "__main__":
    main()
