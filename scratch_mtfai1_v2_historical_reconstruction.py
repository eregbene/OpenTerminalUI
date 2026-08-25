"""MTFAI1 V2 confidence-architecture historical reconstruction (deep confidence audit, Part 3).

Walks REAL, ALREADY-STORED M15 candle history (2018-2026, no new market-data backfill) through
the REAL production V2-aware pipeline (_score_candidate, the same function the live cycle calls)
plus the two fixes shipped this session (_mtfai1_v2_reward_risk_quality, sample-gated performance
recommendation caps), recomputing full 9-component confidence via the REAL confidence.py
compute_trade_confidence -- not a reimplementation.

Point-in-time integrity, two separate concerns:
  1. Signal generation: every input (bars, ADX, HTF trend, structure) comes from replay.py's
     bars_as_of(), which already guarantees no bar whose interval hadn't closed by the replay
     instant is ever used.
  2. Performance memory (the NEW concern this script exists to get right): strategy_performance/
     symbol_performance must reflect ONLY outcomes that had ALREADY RESOLVED before the replay
     instant -- never a future trade's result. Implemented as an explicit, chronologically-
     walked ledger (per symbol AND global) of (resolved_at, outcome_r) pairs; at instant T, only
     entries with resolved_at < T are visible to the recommendation/win-rate calculation, using
     the EXACT SAME AVOID/REDUCE_RISK/NEUTRAL thresholds as performance_monitor.py's real
     recommendation logic (DEMOTE_EXPECTANCY_R_THRESHOLD=-0.05) -- never a second, inconsistent
     definition. This is the ONLY reason this script cannot simply reuse bulk_replay.py's
     existing walker: that module deliberately has no concept of point-in-time-safe performance
     memory (Historical Intelligence's own pattern-statistics module is aggregate-only, not
     walk-forward).

Confirmation bonus = 0 is N/A here: mtfai1 has never gone through mt5_strategies/fusion.py's
confirmation-bonus mechanism (it is scored entirely inline in _score_candidate, no fusion step),
so there is nothing to zero out for this strategy specifically -- noted, not silently ignored.

Known, explicitly-accepted simplifications (documented, not hidden):
  - Quote is approximated as the M15 bar's own close (bid=ask=close, spread=0) when no decision
    snapshot exists for this exact historical instant -- same approximation replay.py's own
    bid/ask contract already documents; affects entry price precision only, never which
    candidates fire or their structural evidence.
  - execution_conditions is scored as "no degradation" (100) throughout -- there is no
    reconstructable historical Redis/infra-outage signal; this is a known upper bound, not a
    fabricated reading.
  - correlation_quality is scored as "no concentration" (100, penalty=0) throughout -- real
    historical cross-account portfolio state for 4 accounts at an arbitrary past instant is not
    reconstructable from stored data; documented as a limitation, not assumed.
  - HI is NEUTRAL throughout, matching the live V2 HI-neutral gate exactly (real code, not an
    approximation).

Output: appends one JSON line per generated V2-eligible candidate (NO_TRADE/low_volatility-
excluded instants are not recorded) to OUTPUT_PATH, flushed periodically so a partial run is
never lost and can be analyzed before full completion.
"""
import asyncio
import json
import statistics as st
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backend.brokers.mt5.autonomous import (
    MT5_MTFAI1_V2_SYMBOLS, _mtfai1_v2_reward_risk_quality, _score_candidate, _swing_level,
)
from backend.brokers.mt5.confidence import compute_trade_confidence
from backend.historical_intelligence import execution_costs
from backend.historical_intelligence.outcomes import _future_candles_with_quality, _MAX_LOOKFORWARD_BARS
from backend.historical_intelligence.replay import bars_as_of, required_lookback
from backend.market_structure.engine import analyze_bars
from backend.mt5_strategies.context import quick_regime

# 2026-08-25: outcomes.py::label_outcome's own _persist() hit a REAL, pre-existing, unrelated
# schema bug (historical_setup_outcomes.commission_cost_provenance is VARCHAR(24), but
# execution_costs.py's own UNKNOWN_REQUIRES_LOT_SIZE constant is 25 chars -- a genuine DB-write
# failure, confirmed directly, not something this script's logic caused). Rather than touch a
# live table's schema for a throwaway analysis run, this reimplements ONLY the pure walk-forward
# R-multiple computation label_outcome already does (same formula, same SL-touched-first tie-
# break, same _future_candles_with_quality source) and skips persistence entirely -- this script
# needs the computed numbers, not a database row. Flagged in the final report as a real, separate,
# unfixed defect (worth its own migration), not silently worked around.
_R_MILESTONES = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0)


def resolve_outcome_no_persist(*, canonical_symbol: str, direction: str, entry: float, stop_loss: float, take_profit: float, entry_time):
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return {"resolution_status": "INSUFFICIENT_FUTURE_DATA"}
    long = direction.upper() == "LONG"
    candles = _future_candles_with_quality(provider="MT5", broker_symbol=canonical_symbol, timeframe="M15", after=entry_time, limit=_MAX_LOOKFORWARD_BARS)
    if not candles:
        return {"resolution_status": "PENDING"}

    max_favorable = 0.0
    max_adverse = 0.0
    resolution = None
    for candle in candles:
        high, low = candle.high, candle.low
        favorable = (high - entry) if long else (entry - low)
        adverse = (entry - low) if long else (high - entry)
        max_favorable = max(max_favorable, favorable)
        max_adverse = max(max_adverse, adverse)
        sl_touched = (low <= stop_loss) if long else (high >= stop_loss)
        tp_touched = (high >= take_profit) if long else (low <= take_profit)
        if sl_touched or tp_touched:
            if sl_touched:
                resolution = {"tp_hit": False, "sl_hit": True, "outcome_r": -1.0, "resolved_at": candle.timestamp}
            else:
                planned_rr = abs(take_profit - entry) / risk
                resolution = {"tp_hit": True, "sl_hit": False, "outcome_r": planned_rr, "resolved_at": candle.timestamp}
            break

    base = {"mfe_r": round(max_favorable / risk, 4), "mae_r": round(max_adverse / risk, 4), "bars_scanned": len(candles)}
    if resolution is not None:
        gross_r = resolution["outcome_r"]
        spread = execution_costs.resolve_spread_cost(canonical_symbol=canonical_symbol, entry_time=entry_time, risk=risk, real_spread=None, db=None)
        commission = execution_costs.resolve_commission_cost_r(db=None)
        net_r = (gross_r - spread.spread_cost_r - (commission.commission_cost_r or 0.0)) if spread.spread_cost_r is not None else None
        base.update({
            "resolution_status": "RESOLVED", "outcome_r": round(gross_r, 4), "net_outcome_r": round(net_r, 4) if net_r is not None else None,
            "tp_hit": resolution["tp_hit"], "sl_hit": resolution["sl_hit"],
            "holding_duration_seconds": (resolution["resolved_at"] - entry_time).total_seconds(),
        })
    elif len(candles) >= _MAX_LOOKFORWARD_BARS:
        last_close = candles[-1].close
        mtm_r = ((last_close - entry) / risk) * (1.0 if long else -1.0)
        base.update({"resolution_status": "RESOLVED", "outcome_r": round(mtm_r, 4), "net_outcome_r": None, "tp_hit": False, "sl_hit": False,
                      "holding_duration_seconds": (candles[-1].timestamp - entry_time).total_seconds()})
    else:
        base["resolution_status"] = "PENDING"
    return base

OUTPUT_PATH = "/data/historical_intelligence/mtfai1_v2_reconstruction.jsonl"
SYMBOLS = sorted(MT5_MTFAI1_V2_SYMBOLS)
START = datetime(2026, 2, 24, tzinfo=timezone.utc)
END = datetime(2026, 8, 24, 18, 44, 0, tzinfo=timezone.utc)  # stop at real V2 activation -- no overlap with live V2-era data
STRIDE = timedelta(minutes=15)
DEMOTE_EXPECTANCY_R_THRESHOLD = -0.05

ledger_global: list[tuple[datetime, float]] = []
ledger_by_symbol: dict[str, list[tuple[datetime, float]]] = defaultdict(list)


def _recommendation_memory(ledger: list[tuple[datetime, float]], at: datetime) -> dict | None:
    resolved = [r for t, r in ledger if t < at]
    if not resolved:
        return None
    win_rate = sum(1 for r in resolved if r > 0) / len(resolved)
    expectancy = st.fmean(resolved)
    negative = expectancy < DEMOTE_EXPECTANCY_R_THRESHOLD
    mildly_negative = expectancy < 0
    recommendation = "AVOID" if negative else ("REDUCE_RISK" if mildly_negative else "NEUTRAL")
    return {"closed_trade_count": len(resolved), "win_rate": win_rate, "recommendation": recommendation, "expectancy": expectancy}


async def process_instant(symbol: str, at: datetime) -> dict | None:
    m15 = await bars_as_of(canonical_symbol=symbol, broker_symbol=symbol, timeframe="M15", at=at, count=required_lookback(timeframe="M15", strategy_ids=["mtfai1"]))
    h1 = await bars_as_of(canonical_symbol=symbol, broker_symbol=symbol, timeframe="H1", at=at, count=required_lookback(timeframe="H1", strategy_ids=["mtfai1"]))
    h4 = await bars_as_of(canonical_symbol=symbol, broker_symbol=symbol, timeframe="H4", at=at, count=required_lookback(timeframe="H4", strategy_ids=["mtfai1"]))
    if len(m15) < 60 or len(h1) < 20 or len(h4) < 20:
        return None

    class _Quote:
        bid = m15[-1].close
        ask = m15[-1].close
        spread = Decimal("0")

    score, direction, geometry = _score_candidate(_Quote(), m15, h1, h4, symbol)
    if direction == "NO_TRADE":
        return None

    m15_rows = [c.model_dump(mode="json") for c in m15]
    regime_info = quick_regime(m15_rows)
    regime = str(regime_info.get("regime") or "insufficient_data")
    if regime == "low_volatility":
        return None

    entry, stop, target = Decimal(geometry["entry"]), Decimal(geometry["stop_loss"]), Decimal(geometry["take_profit"])
    atr = Decimal(geometry["atr"])
    opposing_structure = _swing_level(m15, "SHORT" if direction == "LONG" else "LONG")
    rr_score, rr_breakdown = _mtfai1_v2_reward_risk_quality(
        direction=direction, entry=entry, stop=stop, target=target,
        tp_basis=geometry.get("take_profit_basis", ""), atr=atr, opposing_structure=opposing_structure,
    )

    try:
        snapshot = analyze_bars(m15_rows, symbol=symbol, timeframe="M15")
        entry_quality = {
            "status": "ok", "total_score": snapshot.score.total_score if snapshot.score else None,
            "positive_contributors": [], "negative_contributors": [], "trend_state": str(snapshot.trend.state) if snapshot.trend else None,
        }
    except Exception:
        entry_quality = {"status": "unavailable", "total_score": None, "positive_contributors": [], "negative_contributors": [], "trend_state": None}

    symbol_mem = _recommendation_memory(ledger_by_symbol[symbol], at)
    global_mem = _recommendation_memory(ledger_global, at)

    candidate = {
        "canonical_pair": symbol, "broker_symbol": symbol, "direction": direction,
        "ranking_score": score,
        "context": {
            "risk_reward": geometry["risk_reward"], "atr": geometry["atr"], "spread": geometry["spread"],
            "timestamp": at.isoformat(),
            "trend_quality_score": geometry.get("trend_quality_score"),
            "trend_quality_breakdown": geometry.get("trend_quality_breakdown"),
            "reward_risk_quality_score": rr_score,
            "reward_risk_quality_breakdown": rr_breakdown,
        },
    }
    confidence = compute_trade_confidence(
        candidate=candidate, entry_quality=entry_quality, symbol_memory=symbol_mem, global_memory=global_mem,
        correlation_penalty_points=0.0, correlated_symbols=[], degraded_execution_flags=[], now=at,
    )

    outcome = resolve_outcome_no_persist(
        canonical_symbol=symbol, direction=direction,
        entry=float(entry), stop_loss=float(stop), take_profit=float(target), entry_time=at,
    )
    outcome_r = outcome.get("net_outcome_r") if outcome.get("net_outcome_r") is not None else outcome.get("outcome_r")
    resolved_at_estimate = at + timedelta(seconds=outcome.get("holding_duration_seconds") or 0) if outcome.get("resolution_status") == "RESOLVED" else None

    if outcome_r is not None and resolved_at_estimate is not None:
        ledger_global.append((resolved_at_estimate, outcome_r))
        ledger_by_symbol[symbol].append((resolved_at_estimate, outcome_r))

    return {
        "symbol": symbol, "at": at.isoformat(), "direction": direction,
        "overall_confidence": confidence["overall_score"], "band": confidence["band"],
        "components": confidence["components"],
        "regime": regime, "risk_reward": geometry["risk_reward"], "tp_basis": geometry.get("take_profit_basis"),
        "resolution_status": outcome.get("resolution_status"), "outcome_r": outcome.get("outcome_r"),
        "net_outcome_r": outcome.get("net_outcome_r"), "mfe_r": outcome.get("mfe_r"), "mae_r": outcome.get("mae_r"),
        "tp_hit": outcome.get("tp_hit"), "sl_hit": outcome.get("sl_hit"),
    }


CHECKPOINT_PATH = "/data/historical_intelligence/mtfai1_v2_reconstruction_checkpoint.json"


def _load_checkpoint() -> dict | None:
    try:
        with open(CHECKPOINT_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def _write_checkpoint(symbol: str, at: datetime) -> None:
    tmp = CHECKPOINT_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"symbol": symbol, "at": at.isoformat()}, f)
    import os
    os.replace(tmp, CHECKPOINT_PATH)  # atomic -- never leaves a half-written checkpoint


async def main():
    # 2026-08-25: resume support, added after this run was killed twice by unrelated container
    # recreates (each `docker exec -d` detached process dies when the container is recreated;
    # the /data volume survives, the process does not). Checkpoint is the (symbol, at) of the
    # last instant SCANNED (not just the last candidate WRITTEN -- candidates are sparse, e.g.
    # USDCAD scanned ~4 months with zero candidates before this died the first time, so anchoring
    # resume on the JSONL's last row alone would have silently restarted USDCAD from scratch).
    # Written atomically (temp file + os.replace) every 200 instants and at symbol completion,
    # matching the existing progress-log cadence -- never more than ~200 instants' worth of
    # redundant rescanning on a resume, and OUTPUT_PATH's append-only writes mean a handful of
    # re-scanned instants can at worst duplicate a few JSONL rows, never lose data.
    checkpoint = _load_checkpoint()
    resume_symbol = checkpoint["symbol"] if checkpoint else None
    resume_at = datetime.fromisoformat(checkpoint["at"]) + STRIDE if checkpoint else None
    skipping = resume_symbol is not None

    t0 = time.perf_counter()
    n_processed = 0
    n_candidates = 0
    with open(OUTPUT_PATH, "a") as f:
        for symbol in SYMBOLS:
            if skipping:
                if symbol != resume_symbol:
                    print(f"resume: skipping already-complete symbol {symbol}", flush=True)
                    continue
                at = resume_at
                skipping = False
                print(f"resume: continuing {symbol} from {at.isoformat()} (checkpoint was {checkpoint['symbol']}@{checkpoint['at']})", flush=True)
            else:
                at = START
            while at < END:
                try:
                    result = await process_instant(symbol, at)
                except Exception as exc:
                    result = None
                    print(f"ERROR {symbol} {at.isoformat()}: {exc.__class__.__name__}: {exc}", flush=True)
                n_processed += 1
                if result is not None:
                    n_candidates += 1
                    f.write(json.dumps(result, default=str) + "\n")
                    f.flush()
                if n_processed % 200 == 0:
                    elapsed = time.perf_counter() - t0
                    print(f"progress: processed={n_processed} candidates={n_candidates} symbol={symbol} at={at.isoformat()} elapsed={elapsed:.0f}s avg={elapsed/n_processed:.3f}s/instant", flush=True)
                    _write_checkpoint(symbol, at)
                at += STRIDE
            _write_checkpoint(symbol, at - STRIDE)
    print(f"DONE: processed={n_processed} candidates={n_candidates} elapsed={time.perf_counter()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
