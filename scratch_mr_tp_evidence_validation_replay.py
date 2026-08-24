"""Focused 3-year point-in-time-safe historical validation for mean_reversion/trend_pullback's
Steps 1-4 evidence fields, per user request. Recomputes ONLY these two strategies -- no other
strategy, no full corpus rebuild. Uses bars_as_of() (the real strict no-lookahead gate) + real
build_strategy_context()/evaluate_mean_reversion()/evaluate_trend_pullback() -- same functions
the live scheduler calls. Does NOT persist to historical_pattern_fingerprints/historical_setup_
outcomes (those are real production tables; this run includes COUNTERFACTUAL candidates -- e.g.
trend_pullback outside its regime gate -- that never happened in production and must never be
written there). Outcome labeling mirrors outcomes.py::label_outcome's exact algorithm (same
constants, same _future_candles_with_quality point-in-time-safe candle source) without the
persist step.

For trend_pullback: calls evaluate_trend_pullback(ctx) for EVERY regime (bypassing the
orchestrator's regime_compatible() gate), tagging each result with ctx.regime/ctx.market_regime
so "current production" (regime in {trending_up,trending_down}) vs "shadow widened" (all
regimes) are sliced from ONE pass, never two separate runs of the same instant.

For mean_reversion: Path A = evaluate_mean_reversion(ctx) with the production ctx.regime gate
applied manually here (since calling the evaluator directly bypasses the orchestrator's own
check). Path B = a test-only, NOT-in-production stretch+location+confirmation check, evaluated
independently at the same instant regardless of whether Path A also fired -- so "Path-B-only"
candidates (ones Path A would have missed) can be isolated.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import asyncio

from backend.historical_intelligence.outcomes import _IMMEDIATE_FAILURE_ADVERSE_FRACTION, _MAX_LOOKFORWARD_BARS, _R_MILESTONES, _future_candles_with_quality
from backend.historical_intelligence.replay import bars_as_of, required_lookback, _MIN_BARS
from backend.mt5_strategies.context import build_strategy_context
from backend.mt5_strategies.families.mean_reversion import evaluate_mean_reversion
from backend.mt5_strategies.families.trend_pullback import evaluate_trend_pullback
from backend.mt5_strategies.families._shared import _location_quality_mean_reversion
from backend.mt5_strategies.models import regime_compatible

ALL_SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "XAUUSD")
STRIDE = timedelta(minutes=15)
# Last 6 months, provider MT5: rescoped again per explicit user request (3yr, then 1.5yr, both
# "still much" -- last 6 months, same 4 symbols + gold). Well within the already-verified-dense
# MT5-provider coverage window (2025-2026), same provider the live system itself trades on.
PROVIDER = "MT5"
END = datetime.now(timezone.utc)
START = END - timedelta(days=182)

MR_STRETCH_ATR = 2.0          # "materially large deviation" -- one economically sensible value, not searched
MR_LOCATION_MIN = 12.0        # at least 1 solid location factor (see _LOCATION_QUALITY_BY_FACTOR_COUNT)
OUT_DIR = Path("/data/historical_intelligence/mr_tp_evidence_validation")


def _label_outcome_no_persist(*, canonical_symbol: str, broker_symbol: str, direction: str, entry: float, stop_loss: float,
                               take_profit: float, entry_time: datetime, provider: str = "MT5") -> dict:
    """Mirrors outcomes.py::label_outcome's exact walk-forward algorithm (same constants, same
    point-in-time-safe candle source _future_candles_with_quality) WITHOUT the _persist() call --
    this run includes counterfactual candidates that must never land in the real
    historical_setup_outcomes table."""
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return {"resolution_status": "INSUFFICIENT_FUTURE_DATA"}
    long = direction.upper() == "LONG"
    candles = _future_candles_with_quality(provider=provider, broker_symbol=broker_symbol, timeframe="M15", after=entry_time, limit=_MAX_LOOKFORWARD_BARS)
    if not candles:
        return {"resolution_status": "PENDING"}

    max_favorable = 0.0
    max_adverse = 0.0
    milestone_times: dict[float, datetime] = {}
    reached_before_adverse_failure = False
    resolution = None

    for candle in candles:
        high, low, close = candle.high, candle.low, candle.close
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
            if sl_touched:
                resolution = {"outcome_r": -1.0, "tp_hit": False, "sl_hit": True}
            else:
                resolution = {"outcome_r": abs(take_profit - entry) / risk, "tp_hit": True, "sl_hit": False}
            break

    immediate_failure = (max_adverse / risk >= _IMMEDIATE_FAILURE_ADVERSE_FRACTION) and not reached_before_adverse_failure
    base = {
        "mfe_r": round(max_favorable / risk, 4), "mae_r": round(max_adverse / risk, 4),
        "immediate_failure": immediate_failure,
        "reached_0_5r": 0.5 in milestone_times, "reached_1r": 1.0 in milestone_times, "reached_2r": 2.0 in milestone_times,
        "bars_scanned": len(candles),
    }
    if resolution is not None:
        base["outcome_r"] = round(resolution["outcome_r"], 4)
        base["tp_hit"] = resolution["tp_hit"]
        base["sl_hit"] = resolution["sl_hit"]
        base["resolution_status"] = "RESOLVED"
    elif len(candles) >= _MAX_LOOKFORWARD_BARS:
        last_close = candles[-1].close
        mtm_r = ((last_close - entry) / risk) * (1.0 if long else -1.0)
        base.update({"outcome_r": round(mtm_r, 4), "tp_hit": False, "sl_hit": False, "resolution_status": "RESOLVED"})
    else:
        base["resolution_status"] = "PENDING"
    return base


def _mr_strong_confirmation(ctx, direction: str) -> bool:
    """Test-only (NOT production code): best-of {sweep, CHoCH, displacement} in the reversal
    direction, recent lookback -- mirrors the spec's Strong Confirmation tier (section 4.3)."""
    reversal_direction = "bullish" if direction == "LONG" else "bearish"
    trapping_side = "sell_side" if direction == "LONG" else "buy_side"
    recent_bar_floor = max(0, len(ctx.m15_rows) - 1 - 5)
    sweep = any(s.side == trapping_side and s.bar_index >= recent_bar_floor for s in ctx.m15_snapshot.liquidity_sweeps)
    choch = any(b.break_kind in {"choch", "mss"} and b.direction == reversal_direction and b.bar_index >= recent_bar_floor for b in ctx.m15_snapshot.breaks)
    displacement = any(d.direction == reversal_direction and d.bar_index >= recent_bar_floor for d in ctx.m15_snapshot.displacements)
    return sweep or choch or displacement


def _mr_path_b_candidate(ctx) -> dict | None:
    """Test-only stretch+location+confirmation check, independent of RSI. Returns a dict with
    direction/entry/stop/target/evidence when all three conditions hold, else None."""
    from backend.mt5_strategies.families._shared import _closes, _dynamic_stop
    from backend.core.technicals import ema as _ema_series
    closes = _closes(ctx.m15_rows)
    if len(closes) < 30 or not ctx.atr_m15:
        return None
    price = float(closes.iloc[-1])
    ema20 = float(_ema_series(closes, 20).iloc[-1])
    atr = float(ctx.atr_m15)
    if atr <= 0:
        return None
    stretch = abs(price - ema20) / atr
    if stretch < MR_STRETCH_ATR:
        return None
    direction = "SHORT" if price > ema20 else "LONG"   # fade the stretch, same convention as Path A
    location = _location_quality_mean_reversion(ctx, direction=direction, price=price, atr=atr)
    if location["location_quality_score"] < MR_LOCATION_MIN:
        return None
    if not _mr_strong_confirmation(ctx, direction):
        return None
    entry = Decimal(str(price))
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, None, ctx.atr_m15, min_atr_mult=1.2, max_atr_mult=1.2)
    if stop is None:
        return None
    target = entry + ctx.atr_m15 * Decimal("1.8") if direction == "LONG" else entry - ctx.atr_m15 * Decimal("1.8")
    return {"direction": direction, "entry": float(entry), "stop": float(stop), "target": float(target), "stretch_atr": round(stretch, 3), **location}


async def replay_symbol(symbol: str, *, start: datetime, end: datetime, progress_every: int = 2000) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{symbol.lower()}.json"
    progress_path = OUT_DIR / f"{symbol.lower()}_progress.json"
    log_path = OUT_DIR / f"{symbol.lower()}.log"

    def log(msg: str) -> None:
        with open(log_path, "a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} {msg}\n")

    at = start
    if progress_path.exists():
        try:
            saved = json.loads(progress_path.read_text())
            resume_at = datetime.fromisoformat(saved["last_completed_at"])
            at = resume_at + STRIDE
            log(f"resuming from {at.isoformat()}")
        except Exception as exc:
            log(f"progress file unreadable, starting fresh: {exc!r}")

    results: list[dict] = []
    if out_path.exists():
        try:
            results = json.loads(out_path.read_text())
            log(f"loaded {len(results)} existing results")
        except Exception:
            results = []

    instants = 0
    t0 = time.perf_counter()
    while at < end:
        instants += 1
        try:
            m15 = await bars_as_of(canonical_symbol=symbol, broker_symbol=symbol, timeframe="M15", at=at, count=required_lookback(timeframe="M15"), provider=PROVIDER)
            h1 = await bars_as_of(canonical_symbol=symbol, broker_symbol=symbol, timeframe="H1", at=at, count=required_lookback(timeframe="H1"), provider=PROVIDER)
            h4 = await bars_as_of(canonical_symbol=symbol, broker_symbol=symbol, timeframe="H4", at=at, count=required_lookback(timeframe="H4"), provider=PROVIDER)
            if len(m15) < _MIN_BARS["M15"] or len(h1) < _MIN_BARS["H1"] or len(h4) < _MIN_BARS["H4"]:
                at += STRIDE
                continue
            last_close = m15[-1].close
            from backend.historical_intelligence.replay import _ReplayQuote
            quote = _ReplayQuote(bid=last_close, ask=last_close, spread=Decimal("0"))
            ctx = build_strategy_context(
                symbol=symbol, broker_symbol=symbol,
                m15_rows=[c.model_dump(mode="json") for c in m15], h1_rows=[c.model_dump(mode="json") for c in h1], h4_rows=[c.model_dump(mode="json") for c in h4],
                bid=quote.bid, ask=quote.ask, spread=quote.spread, now=at,
            )
        except Exception as exc:
            log(f"context build failed at {at.isoformat()}: {exc.__class__.__name__}: {exc}")
            at += STRIDE
            continue

        if ctx is None:
            at += STRIDE
            continue

        entry_time = m15[-1].time
        if entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=timezone.utc)

        # ---- trend_pullback: called for EVERY regime, tagged for later slicing ----
        try:
            tp_sig = evaluate_trend_pullback(ctx)
        except Exception as exc:
            log(f"trend_pullback eval failed at {at.isoformat()}: {exc.__class__.__name__}: {exc}")
            tp_sig = None
        if tp_sig is not None and tp_sig.direction in ("LONG", "SHORT") and tp_sig.valid:
            outcome = _label_outcome_no_persist(canonical_symbol=symbol, broker_symbol=symbol, direction=tp_sig.direction,
                                                 entry=tp_sig.proposed_entry, stop_loss=tp_sig.stop_loss, take_profit=tp_sig.take_profit, entry_time=entry_time, provider=PROVIDER)
            results.append({
                "strategy": "trend_pullback", "symbol": symbol, "direction": tp_sig.direction, "entry_time": entry_time.isoformat(),
                "regime": ctx.regime, "market_regime": ctx.market_regime,
                "regime_compatible_current_prod": regime_compatible("trend_pullback", ctx.regime) if ctx.regime in {"trending_up", "trending_down"} else False,
                "evidence": tp_sig.evidence, "outcome": outcome,
            })

        # ---- mean_reversion Path A: production RSI trigger, regime gate applied manually ----
        try:
            mr_sig = evaluate_mean_reversion(ctx)
        except Exception as exc:
            log(f"mean_reversion eval failed at {at.isoformat()}: {exc.__class__.__name__}: {exc}")
            mr_sig = None
        path_a_fired = mr_sig is not None and mr_sig.direction in ("LONG", "SHORT") and mr_sig.valid
        if path_a_fired:
            outcome = _label_outcome_no_persist(canonical_symbol=symbol, broker_symbol=symbol, direction=mr_sig.direction,
                                                 entry=mr_sig.proposed_entry, stop_loss=mr_sig.stop_loss, take_profit=mr_sig.take_profit, entry_time=entry_time, provider=PROVIDER)
            results.append({
                "strategy": "mean_reversion_path_a", "symbol": symbol, "direction": mr_sig.direction, "entry_time": entry_time.isoformat(),
                "regime": ctx.regime, "market_regime": ctx.market_regime,
                "regime_compatible_current_prod": ctx.regime in {"ranging", "low_volatility"},
                "evidence": mr_sig.evidence, "outcome": outcome,
            })

        # ---- mean_reversion Path B: test-only stretch+location+confirmation, independent of RSI ----
        try:
            path_b = _mr_path_b_candidate(ctx)
        except Exception as exc:
            log(f"mean_reversion path B failed at {at.isoformat()}: {exc.__class__.__name__}: {exc}")
            path_b = None
        if path_b is not None:
            from backend.mt5_strategies.families._shared import _wick_rejection_score
            outcome = _label_outcome_no_persist(canonical_symbol=symbol, broker_symbol=symbol, direction=path_b["direction"],
                                                 entry=path_b["entry"], stop_loss=path_b["stop"], take_profit=path_b["target"], entry_time=entry_time, provider=PROVIDER)
            results.append({
                "strategy": "mean_reversion_path_b", "symbol": symbol, "direction": path_b["direction"], "entry_time": entry_time.isoformat(),
                "regime": ctx.regime, "market_regime": ctx.market_regime, "path_a_also_fired": path_a_fired,
                "evidence": {**path_b, "wick_rejection_score": _wick_rejection_score(ctx, path_b["direction"])}, "outcome": outcome,
            })

        if instants % progress_every == 0:
            elapsed = time.perf_counter() - t0
            log(f"progress: instant={at.isoformat()} walked={instants} results={len(results)} elapsed={elapsed:.0f}s")
            out_path.write_text(json.dumps(results, default=str))
            progress_path.write_text(json.dumps({"last_completed_at": at.isoformat()}))

        at += STRIDE

    out_path.write_text(json.dumps(results, default=str))
    progress_path.write_text(json.dumps({"last_completed_at": (end - STRIDE).isoformat(), "DONE": True}))
    log(f"ALL DONE: walked={instants} results={len(results)} elapsed={time.perf_counter()-t0:.0f}s")
    print(f"{symbol}: ALL DONE walked={instants} results={len(results)}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=",".join(ALL_SYMBOLS))
    args = parser.parse_args()
    symbols = args.symbols.split(",")
    for sym in symbols:
        asyncio.run(replay_symbol(sym, start=START, end=END))
