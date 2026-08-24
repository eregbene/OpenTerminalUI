"""FAST 6-month point-in-time-safe validation for fx_relative_momentum. Deliberately fast, not a
multi-day corpus rebuild: walks at H4-bar granularity (currency strength only changes when a new
H4 bar closes anyway -- evaluating every M15 instant would recompute the identical strength ~16
times per H4 bar for no benefit). All 9 FX pairs (XAUUSD excluded -- not a currency pair;
USDCAD excluded as a CANDIDATE since CAD isn't in the ranked 7-currency set, but still fetched
and used as a strength INPUT for USD). Tests 3 standard, non-tuned momentum horizons (SHORT/
MEDIUM/LONG, see currency_strength.py) from ONE walk (compute once, tag three ways). No
persistence to production tables -- this strategy is DISABLED, all candidates here are either
counterfactual or standalone-evaluated.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import asyncio

from backend.historical_intelligence.outcomes import _IMMEDIATE_FAILURE_ADVERSE_FRACTION, _MAX_LOOKFORWARD_BARS, _R_MILESTONES, _future_candles_with_quality
from backend.historical_intelligence.replay import bars_as_of, required_lookback, _MIN_BARS, _ReplayQuote
from backend.mt5_strategies.context import build_strategy_context
from backend.mt5_strategies.currency_strength import HORIZONS, _PAIR_CURRENCIES, compute_currency_strength
from backend.mt5_strategies.families.fx_relative_momentum import evaluate_fx_relative_momentum

ALL_FX_SYMBOLS = tuple(_PAIR_CURRENCIES.keys())  # 9 pairs, includes USDCAD (strength input only)
CANDIDATE_SYMBOLS = tuple(s for s in ALL_FX_SYMBOLS if s != "USDCAD")  # 8 tradeable (CAD not ranked)
PROVIDER = "MT5"
END = datetime.now(timezone.utc)
START = END - timedelta(days=182)
H4_STRIDE = timedelta(hours=4)
OUT_DIR = Path("/data/historical_intelligence/fx_relative_momentum_validation")


def _label_outcome_no_persist(*, canonical_symbol, broker_symbol, direction, entry, stop_loss, take_profit, entry_time, provider=PROVIDER) -> dict:
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return {"resolution_status": "INSUFFICIENT_FUTURE_DATA"}
    long = direction.upper() == "LONG"
    candles = _future_candles_with_quality(provider=provider, broker_symbol=broker_symbol, timeframe="M15", after=entry_time, limit=_MAX_LOOKFORWARD_BARS)
    if not candles:
        return {"resolution_status": "PENDING"}
    max_favorable = max_adverse = 0.0
    milestone_times: dict[float, datetime] = {}
    reached_before_adverse_failure = False
    resolution = None
    for candle in candles:
        high, low = candle.high, candle.low
        favorable = (high - entry) if long else (entry - low)
        adverse = (entry - low) if long else (high - entry)
        max_favorable = max(max_favorable, favorable)
        max_adverse = max(max_adverse, adverse)
        favorable_r_now = max_favorable / risk
        for milestone in _R_MILESTONES:
            if milestone not in milestone_times and favorable_r_now >= milestone:
                milestone_times[milestone] = candle.timestamp
        if not reached_before_adverse_failure and favorable_r_now >= 0.25:
            reached_before_adverse_failure = True
        sl_touched = (low <= stop_loss) if long else (high >= stop_loss)
        tp_touched = (high >= take_profit) if long else (low <= take_profit)
        if sl_touched or tp_touched:
            resolution = {"outcome_r": -1.0, "tp_hit": False, "sl_hit": True} if sl_touched else \
                {"outcome_r": abs(take_profit - entry) / risk, "tp_hit": True, "sl_hit": False}
            break
    immediate_failure = (max_adverse / risk >= _IMMEDIATE_FAILURE_ADVERSE_FRACTION) and not reached_before_adverse_failure
    base = {
        "mfe_r": round(max_favorable / risk, 4), "mae_r": round(max_adverse / risk, 4), "immediate_failure": immediate_failure,
        "reached_0_5r": 0.5 in milestone_times, "reached_1r": 1.0 in milestone_times, "reached_2r": 2.0 in milestone_times,
        "reached_3r": (max_favorable / risk) >= 3.0, "bars_scanned": len(candles),
    }
    if resolution is not None:
        base["outcome_r"] = round(resolution["outcome_r"], 4)
        base["tp_hit"], base["sl_hit"] = resolution["tp_hit"], resolution["sl_hit"]
        base["resolution_status"] = "RESOLVED"
    elif len(candles) >= _MAX_LOOKFORWARD_BARS:
        last_close = candles[-1].close
        mtm_r = ((last_close - entry) / risk) * (1.0 if long else -1.0)
        base.update({"outcome_r": round(mtm_r, 4), "tp_hit": False, "sl_hit": False, "resolution_status": "RESOLVED"})
    else:
        base["resolution_status"] = "PENDING"
    return base


async def run() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = OUT_DIR / "run.log"

    def log(msg: str) -> None:
        with open(log_path, "a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} {msg}\n")

    out_path = OUT_DIR / "results.json"
    progress_path = OUT_DIR / "progress.json"
    at = START
    results: list[dict] = []
    if out_path.exists():
        try:
            results = json.loads(out_path.read_text())
            log(f"loaded {len(results)} existing results")
        except Exception:
            results = []
    if progress_path.exists():
        try:
            saved = json.loads(progress_path.read_text())
            at = datetime.fromisoformat(saved["last_completed_at"]) + H4_STRIDE
            log(f"resuming from {at.isoformat()}")
        except Exception as exc:
            log(f"progress unreadable, starting fresh: {exc!r}")

    instants = 0
    t0 = time.perf_counter()
    while at < END:
        instants += 1
        try:
            bars_by_symbol = {}
            for sym in ALL_FX_SYMBOLS:
                h4 = await bars_as_of(canonical_symbol=sym, broker_symbol=sym, timeframe="H4", at=at, count=required_lookback(timeframe="H4"), provider=PROVIDER)
                bars_by_symbol[sym] = [c.model_dump(mode="json") for c in h4]
        except Exception as exc:
            log(f"strength bar fetch failed at {at.isoformat()}: {exc.__class__.__name__}: {exc}")
            at += H4_STRIDE
            continue

        strengths = {h: compute_currency_strength(bars_by_symbol, h) for h in HORIZONS}

        for sym in CANDIDATE_SYMBOLS:
            try:
                m15 = await bars_as_of(canonical_symbol=sym, broker_symbol=sym, timeframe="M15", at=at, count=required_lookback(timeframe="M15"), provider=PROVIDER)
                h1 = await bars_as_of(canonical_symbol=sym, broker_symbol=sym, timeframe="H1", at=at, count=required_lookback(timeframe="H1"), provider=PROVIDER)
                h4 = bars_by_symbol[sym][-required_lookback(timeframe="H4"):] if len(bars_by_symbol[sym]) >= required_lookback(timeframe="H4") else bars_by_symbol[sym]
                if len(m15) < _MIN_BARS["M15"] or len(h1) < _MIN_BARS["H1"] or len(bars_by_symbol[sym]) < _MIN_BARS["H4"]:
                    continue
                last_close = m15[-1].close
                quote = _ReplayQuote(bid=last_close, ask=last_close, spread=Decimal("0"))
                ctx = build_strategy_context(
                    symbol=sym, broker_symbol=sym, m15_rows=[c.model_dump(mode="json") for c in m15],
                    h1_rows=[c.model_dump(mode="json") for c in h1], h4_rows=bars_by_symbol[sym],
                    bid=quote.bid, ask=quote.ask, spread=quote.spread, now=at,
                )
                if ctx is None:
                    continue
                entry_time = m15[-1].time
                if entry_time.tzinfo is None:
                    entry_time = entry_time.replace(tzinfo=timezone.utc)
                for horizon_name, cs in strengths.items():
                    ctx_h = ctx.__class__(**{**ctx.__dict__, "currency_strength": cs})
                    sig = evaluate_fx_relative_momentum(ctx_h)
                    if sig.direction not in ("LONG", "SHORT") or not sig.valid:
                        continue
                    outcome = _label_outcome_no_persist(canonical_symbol=sym, broker_symbol=sym, direction=sig.direction,
                                                         entry=sig.proposed_entry, stop_loss=sig.stop_loss, take_profit=sig.take_profit, entry_time=entry_time)
                    results.append({
                        "symbol": sym, "direction": sig.direction, "entry_time": entry_time.isoformat(),
                        "horizon": horizon_name, "regime": ctx.regime, "market_regime": ctx.market_regime,
                        "evidence": sig.evidence, "outcome": outcome,
                    })
            except Exception as exc:
                log(f"eval failed {sym} at {at.isoformat()}: {exc.__class__.__name__}: {exc}")

        if instants % 50 == 0:
            elapsed = time.perf_counter() - t0
            log(f"progress: instant={at.isoformat()} walked={instants} results={len(results)} elapsed={elapsed:.0f}s")
            out_path.write_text(json.dumps(results, default=str))
            progress_path.write_text(json.dumps({"last_completed_at": at.isoformat()}))
        at += H4_STRIDE

    out_path.write_text(json.dumps(results, default=str))
    progress_path.write_text(json.dumps({"last_completed_at": (END - H4_STRIDE).isoformat(), "DONE": True}))
    log(f"ALL DONE: walked={instants} results={len(results)} elapsed={time.perf_counter()-t0:.0f}s")
    print(f"ALL DONE walked={instants} results={len(results)}", flush=True)


if __name__ == "__main__":
    asyncio.run(run())
