"""3-year, 10-symbol, point-in-time-safe historical validation for donchian_trend_follow.
Reuses the exact same proven infrastructure as mr_tp_evidence_validation_replay.py: bars_as_of()
(strict no-lookahead gate), real build_strategy_context()/evaluate_donchian_trend_follow() (the
same functions the live scheduler would call once activated), and outcomes.py's exact walk-
forward outcome algorithm (without persisting -- this strategy is DISABLED in production, its
candidates are counterfactual and must never land in the real historical_setup_outcomes table).

Period/provider: last 6 months (rescoped 2026-08-24 from an original 2020-2023/FOREXSB plan --
"3yr, then 1.5yr, both still much" -- down to 6 months / MT5-provider / 4 symbols+gold, matching
mr_tp_evidence_validation's own rescope), well within the already-verified-dense MT5-provider
coverage window. Chosen for data-availability/turnaround reasons, not after seeing any donchian
results (anti-selection-bias instruction).

Evaluates BREAK_AND_GO (production default) only in this first pass; RETEST_AND_HOLD is a
separate, smallest-safe follow-up run once BREAK_AND_GO's results are in, not run blind
alongside it.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import asyncio

from backend.historical_intelligence.outcomes import _IMMEDIATE_FAILURE_ADVERSE_FRACTION, _MAX_LOOKFORWARD_BARS, _R_MILESTONES, _future_candles_with_quality
from backend.historical_intelligence.replay import bars_as_of, required_lookback, _MIN_BARS, _ReplayQuote
from backend.mt5_strategies.context import build_strategy_context
from backend.mt5_strategies.families.donchian_trend_follow import evaluate_donchian_trend_follow

ALL_SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "XAUUSD")
STRIDE = timedelta(minutes=15)
# Rescoped 2026-08-24 per explicit user request (3yr, then 1.5yr, both "still much" -- last 6
# months, same 4 symbols + gold, matching mr_tp_evidence_validation's own rescope). Provider
# switched from FOREXSB/2020-2023 to MT5/last-6-months -- well within the already-verified-dense
# MT5-provider coverage window (2025-2026), same provider the live system itself trades on.
PROVIDER = "MT5"
END = datetime.now(timezone.utc)
START = END - timedelta(days=182)
OUT_DIR = Path("/data/historical_intelligence/donchian_validation")


def _label_outcome_no_persist(*, canonical_symbol: str, broker_symbol: str, direction: str, entry: float, stop_loss: float,
                               take_profit: float, entry_time: datetime, provider: str = PROVIDER) -> dict:
    """Same algorithm as outcomes.py::label_outcome, without the persist step (counterfactual
    candidate -- this strategy is DISABLED in production)."""
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
        "mfe_r": round(max_favorable / risk, 4), "mae_r": round(max_adverse / risk, 4),
        "immediate_failure": immediate_failure,
        "reached_0_5r": 0.5 in milestone_times, "reached_1r": 1.0 in milestone_times,
        "reached_2r": 2.0 in milestone_times, "reached_3r": 3.0 in milestone_times if 3.0 in _R_MILESTONES else (max_favorable / risk) >= 3.0,
        "reached_5r": (max_favorable / risk) >= 5.0,
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
            at = datetime.fromisoformat(saved["last_completed_at"]) + STRIDE
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

        try:
            sig = evaluate_donchian_trend_follow(ctx)
        except Exception as exc:
            log(f"eval failed at {at.isoformat()}: {exc.__class__.__name__}: {exc}")
            sig = None
        if sig is not None and sig.direction in ("LONG", "SHORT") and sig.valid:
            outcome = _label_outcome_no_persist(canonical_symbol=symbol, broker_symbol=symbol, direction=sig.direction,
                                                 entry=sig.proposed_entry, stop_loss=sig.stop_loss, take_profit=sig.take_profit, entry_time=entry_time)
            results.append({
                "symbol": symbol, "direction": sig.direction, "entry_time": entry_time.isoformat(),
                "regime": ctx.regime, "market_regime": ctx.market_regime,
                "evidence": sig.evidence, "outcome": outcome,
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
    for sym in args.symbols.split(","):
        asyncio.run(replay_symbol(sym, start=START, end=END))
