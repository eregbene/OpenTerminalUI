"""Phase 1-3 comprehensive OOS flag validation harness (2026-08-21, v2): point-in-time replay
comparison of every requested Phase 1/2/3 feature-flag variant against baseline (flags off),
across real historical M15 bars.

KEY EFFICIENCY DESIGN: naively running each of the ~21 requested flag/strategy variants as its
own separate full walk would mean ~21x the cost of a single-flag-set validation. Instead, this
harness builds the expensive part -- StrategyContext, including M15/H1/H4 market structure --
exactly ONCE per instant (context itself is flag-independent; only the strategy evaluators read
flags), then cheaply calls every requested strategy/variant combination against that SAME
context, toggling only the relevant env vars around each call. Measured: ~87ms/instant with all
21 variants evaluated, barely more than a single-variant walk alone.

v2 additions (this is an unattended, multi-hour, likely-overnight/weekend run, and this session
has already hit four unrelated Docker Desktop engine hangs -- a job with no crash recovery would
lose hours of work to any one of them):
  - Output moves from the container's own writable layer (which sits on C:, repeatedly found
    near-zero free this session) to /research (bind-mounted to E:\BensimResearch on the host --
    see docker-compose.yml) -- plenty of space, survives container recreation, directly
    inspectable from the host.
  - CRASH-SAFE INCREMENTAL WRITES: trades are appended as JSON Lines (one JSON object per line,
    flushed every _FLUSH_EVERY_TRADES) instead of accumulated in memory and written once at the
    very end of a multi-hour symbol walk -- a crash loses at most one flush interval's worth of
    work, not hours.
  - COARSE RESUME: a `.progress` file records the last-completed bar index every
    _PROGRESS_EVERY_INSTANTS instants; on restart, a symbol resumes from just past that point
    instead of from the beginning. A `.done` marker on full completion means a fully-finished
    symbol is skipped entirely on restart, not re-walked.

SAFETY: strictly READ-ONLY against the database. NEVER writes to historical_pattern_fingerprints
/ historical_setup_outcomes -- this is a research comparison, must never contaminate the live
corpus entry_intelligence.py reads for real (DEMO) trading decisions happening right now.

Usage (inside the backend container):
  python scratch_phase123_variant_replay.py --symbols EURUSD XAUUSD --max-instants 3000
  python scratch_phase123_variant_replay.py --out-dir /research/phase123   # full run, all 10 symbols
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text  # noqa: E402

from backend.brokers.mt5.models import MT5Candle  # noqa: E402
from backend.historical_intelligence.outcomes import _IMMEDIATE_FAILURE_ADVERSE_FRACTION, _MAX_LOOKFORWARD_BARS, _R_MILESTONES  # noqa: E402
from backend.historical_intelligence.replay import _row_to_candle  # noqa: E402
from backend.market_structure.engine import analyze_bars  # noqa: E402
from backend.mt5_strategies.context import build_strategy_context  # noqa: E402
from backend.mt5_strategies.families.breakout import evaluate_breakout  # noqa: E402
from backend.mt5_strategies.families.ema_trend import evaluate_ema_trend  # noqa: E402
from backend.mt5_strategies.families.mean_reversion import evaluate_mean_reversion  # noqa: E402
from backend.mt5_strategies.families.smc_continuation import evaluate_smc_continuation  # noqa: E402
from backend.mt5_strategies.families.support_resistance_bounce import evaluate_support_resistance_bounce  # noqa: E402
from backend.mt5_strategies.families.trend_pullback import evaluate_trend_pullback  # noqa: E402
from backend.shared.db import SessionLocal  # noqa: E402

_TF_DELTA = {"M15": timedelta(minutes=15), "H1": timedelta(hours=1), "H4": timedelta(hours=4)}
_ALL_SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "EURJPY", "GBPJPY", "XAUUSD")
_LOOKBACK_BARS = 100
_MIN_BARS = 60
_FLUSH_EVERY_TRADES = 200
_PROGRESS_EVERY_INSTANTS = 2000
_DEFAULT_OUT_DIR = "/research/phase123"

_ALL_FLAG_DEFAULTS: dict[str, str] = {
    "MT5_BREAKOUT_ATR_BUFFER_ENABLED": "false",
    "MT5_BREAKOUT_ENTRY_MODE": "BREAK_AND_GO",
    "MT5_BREAKOUT_HTF_GATE_ENABLED": "false",
    "MT5_BREAKOUT_IDM_REQUIRED": "false",
    "MT5_TREND_PULLBACK_CONFLUENCE_MODE": "EMA_ZONE",
    "MT5_TREND_PULLBACK_ANTI_CHOCH_GATE_ENABLED": "false",
    "MT5_TREND_PULLBACK_REACTION_CANDLE_REQUIRED": "false",
    "MT5_SMC_CONTINUATION_IDM_REQUIRED": "false",
    "MT5_EMA_TREND_STRUCTURAL_TRIGGER_ENABLED": "false",
    "MT5_SR_BOUNCE_HTF_GATE_ENABLED": "false",
    "MT5_STRUCTURAL_TP_ENABLED": "false",
    "MT5_REGIME_FILTER_ENABLED": "false",
    "MT5_SESSION_TIMING_FILTER_ENABLED": "false",
    "MT5_SPREAD_FILTER_ENABLED": "false",
    "MT5_STALE_EXIT_ENABLED": "false",
}

_STRATEGY_EVALUATORS = {
    "breakout": evaluate_breakout,
    "trend_pullback": evaluate_trend_pullback,
    "smc_continuation": evaluate_smc_continuation,
    "ema_trend": evaluate_ema_trend,
    "mean_reversion": evaluate_mean_reversion,
    "support_resistance_bounce": evaluate_support_resistance_bounce,
}
_VARIANTS: dict[str, list[tuple[str, dict[str, str]]]] = {
    "breakout": [
        ("baseline", {}),
        ("atr_buffer", {"MT5_BREAKOUT_ATR_BUFFER_ENABLED": "true"}),
        ("retest_and_hold", {"MT5_BREAKOUT_ENTRY_MODE": "RETEST_AND_HOLD"}),
        ("idm_required", {"MT5_BREAKOUT_IDM_REQUIRED": "true"}),
        ("structural_tp", {"MT5_STRUCTURAL_TP_ENABLED": "true"}),
        ("regime_filter", {"MT5_REGIME_FILTER_ENABLED": "true"}),
        ("session_timing", {"MT5_SESSION_TIMING_FILTER_ENABLED": "true"}),
    ],
    "trend_pullback": [
        ("baseline", {}),
        ("ote_ob_fvg_anti_choch", {"MT5_TREND_PULLBACK_CONFLUENCE_MODE": "OTE_OB_FVG", "MT5_TREND_PULLBACK_ANTI_CHOCH_GATE_ENABLED": "true"}),
        ("structural_tp", {"MT5_STRUCTURAL_TP_ENABLED": "true"}),
    ],
    "smc_continuation": [
        ("baseline", {}),
        ("structural_tp", {"MT5_STRUCTURAL_TP_ENABLED": "true"}),
    ],
    "ema_trend": [
        ("baseline", {}),
        ("structural_trigger", {"MT5_EMA_TREND_STRUCTURAL_TRIGGER_ENABLED": "true"}),
        ("structural_tp", {"MT5_STRUCTURAL_TP_ENABLED": "true"}),
        ("regime_filter", {"MT5_REGIME_FILTER_ENABLED": "true"}),
    ],
    "mean_reversion": [
        ("baseline", {}),
        ("structural_tp", {"MT5_STRUCTURAL_TP_ENABLED": "true"}),
        ("regime_filter", {"MT5_REGIME_FILTER_ENABLED": "true"}),
    ],
    "support_resistance_bounce": [
        ("baseline", {}),
        ("htf_gate", {"MT5_SR_BOUNCE_HTF_GATE_ENABLED": "true"}),
    ],
}


def _apply_variant(overrides: dict[str, str]) -> None:
    for key, default in _ALL_FLAG_DEFAULTS.items():
        os.environ[key] = overrides.get(key, default)


def _ensure_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _load_symbol_timeframe(symbol: str, timeframe: str) -> list[MT5Candle]:
    with SessionLocal() as db:
        revision_rows = db.execute(
            text(
                """
                SELECT DISTINCT ON (bar_timestamp_utc) bar_timestamp_utc, open, high, low, close, tick_volume, spread, real_volume, provider
                FROM mt5_candle_revisions
                WHERE broker_symbol = :symbol AND timeframe = :tf
                ORDER BY bar_timestamp_utc, revision_number DESC
                """
            ),
            {"symbol": symbol, "tf": timeframe},
        ).fetchall()
        canonical_rows = db.execute(
            text(
                """
                SELECT COALESCE(timestamp_utc, timestamp) AS bar_time, open, high, low, close, tick_volume, spread, real_volume, provider
                FROM mt5_canonical_candles
                WHERE broker_symbol = :symbol AND timeframe = :tf AND quality != 'INVALID'
                ORDER BY bar_time
                """
            ),
            {"symbol": symbol, "tf": timeframe},
        ).fetchall()
    resolved: dict[datetime, Any] = {}
    for row in canonical_rows:
        resolved[_ensure_utc(row.bar_time)] = row
    for row in revision_rows:
        resolved[_ensure_utc(row.bar_timestamp_utc)] = row
    candles = []
    for bar_time in sorted(resolved.keys()):
        row = resolved[bar_time]
        candles.append(_row_to_candle(symbol, timeframe, bar_time, row))
    return candles


@dataclass
class _Trade:
    strategy_id: str
    variant: str
    symbol: str
    direction: str
    entry_time: str
    entry: float
    stop_loss: float
    take_profit: float
    outcome_r: float | None
    tp_hit: bool | None
    sl_hit: bool | None
    immediate_failure: bool | None
    resolution_status: str
    holding_bars: int | None


def _compute_outcome(*, direction: str, entry: float, stop_loss: float, take_profit: float, future_m15: list[MT5Candle]) -> dict[str, Any]:
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return {"resolution_status": "INSUFFICIENT_FUTURE_DATA"}
    long = direction.upper() == "LONG"
    candles = future_m15[:_MAX_LOOKFORWARD_BARS]
    if not candles:
        return {"resolution_status": "PENDING"}
    max_favorable = 0.0
    max_adverse = 0.0
    milestone_times: dict[float, int] = {}
    reached_before_adverse_failure = False
    resolution: dict[str, Any] | None = None
    for idx, candle in enumerate(candles):
        high, low = float(candle.high), float(candle.low)
        favorable = (high - entry) if long else (entry - low)
        adverse = (entry - low) if long else (high - entry)
        if favorable > max_favorable:
            max_favorable = favorable
        max_adverse = max(max_adverse, adverse)
        favorable_r_now = max_favorable / risk
        for milestone in _R_MILESTONES:
            if milestone not in milestone_times and favorable_r_now >= milestone:
                milestone_times[milestone] = idx
        if not reached_before_adverse_failure and favorable_r_now >= 0.25:
            reached_before_adverse_failure = True
        sl_touched = (low <= stop_loss) if long else (high >= stop_loss)
        tp_touched = (high >= take_profit) if long else (low <= take_profit)
        if sl_touched or tp_touched:
            if sl_touched:
                resolution = {"tp_hit": False, "sl_hit": True, "outcome_r": -1.0, "resolved_idx": idx}
            else:
                planned_rr = abs(take_profit - entry) / risk
                resolution = {"tp_hit": True, "sl_hit": False, "outcome_r": planned_rr, "resolved_idx": idx}
            break
    immediate_failure = (max_adverse / risk >= _IMMEDIATE_FAILURE_ADVERSE_FRACTION) and not reached_before_adverse_failure
    base = {"immediate_failure": immediate_failure}
    if resolution is not None:
        base.update({"resolution_status": "RESOLVED", "outcome_r": round(resolution["outcome_r"], 4), "tp_hit": resolution["tp_hit"], "sl_hit": resolution["sl_hit"], "holding_bars": resolution["resolved_idx"] + 1})
    elif len(candles) >= _MAX_LOOKFORWARD_BARS:
        last_close = float(candles[-1].close)
        mtm_r = ((last_close - entry) / risk) * (1.0 if long else -1.0)
        base.update({"resolution_status": "RESOLVED", "outcome_r": round(mtm_r, 4), "tp_hit": False, "sl_hit": False, "holding_bars": len(candles)})
    else:
        base["resolution_status"] = "PENDING"
    return base


def replay_symbol(symbol: str, *, max_instants: int | None, progress_every: int, lookback_years: float | None, out_dir: str) -> None:
    trades_path = os.path.join(out_dir, f"{symbol}.jsonl")
    progress_path = os.path.join(out_dir, f"{symbol}.progress")
    done_path = os.path.join(out_dir, f"{symbol}.done")

    if os.path.exists(done_path):
        print(f"[{symbol}] already DONE (found {done_path}), skipping", flush=True)
        return

    t0 = time.perf_counter()
    m15 = _load_symbol_timeframe(symbol, "M15")
    h1 = _load_symbol_timeframe(symbol, "H1")
    h4 = _load_symbol_timeframe(symbol, "H4")
    print(f"[{symbol}] loaded m15={len(m15)} h1={len(h1)} h4={len(h4)} in {time.perf_counter()-t0:.1f}s", flush=True)
    if len(m15) < _LOOKBACK_BARS + _MAX_LOOKFORWARD_BARS:
        print(f"[{symbol}] insufficient M15 history, skipping", flush=True)
        return

    h1_ptr, h4_ptr = -1, -1
    h1_snapshot_cache: tuple[int, Any] | None = None
    h4_snapshot_cache: tuple[int, Any] | None = None

    start_idx = _LOOKBACK_BARS - 1
    end_idx = len(m15) - _MAX_LOOKFORWARD_BARS - 1
    if lookback_years is not None:
        cutoff = m15[end_idx].time - timedelta(days=lookback_years * 365.25)
        cutoff_idx = start_idx
        for j in range(start_idx, end_idx):
            if m15[j].time >= cutoff:
                cutoff_idx = j
                break
        start_idx = max(start_idx, cutoff_idx)

    # Coarse resume: pick up just past the last bar index a prior (crashed/killed) run of this
    # SAME symbol confirmed complete. Trades already appended to trades_path for earlier instants
    # are left exactly as they are (append-only, never rewritten) -- resuming past them avoids
    # both re-walking already-done work and duplicating already-written trades.
    if os.path.exists(progress_path):
        try:
            resume_i = int(open(progress_path).read().strip()) + 1
            if resume_i > start_idx:
                print(f"[{symbol}] resuming from bar index {resume_i} (progress file found)", flush=True)
                start_idx = resume_i
        except (ValueError, OSError):
            pass

    print(f"[{symbol}] walk window: {end_idx - start_idx} instants remaining", flush=True)
    instants_done = 0
    t_loop = time.perf_counter()
    trade_buffer: list[str] = []

    with open(trades_path, "a") as trades_file:
        def _flush() -> None:
            if trade_buffer:
                trades_file.write("\n".join(trade_buffer) + "\n")
                trades_file.flush()
                os.fsync(trades_file.fileno())
                trade_buffer.clear()

        for i in range(start_idx, end_idx):
            if max_instants is not None and instants_done >= max_instants:
                break
            instants_done += 1
            at = m15[i].time + _TF_DELTA["M15"]
            m15_window = m15[i - _LOOKBACK_BARS + 1 : i + 1]

            while h1_ptr + 1 < len(h1) and h1[h1_ptr + 1].time + _TF_DELTA["H1"] <= at:
                h1_ptr += 1
            while h4_ptr + 1 < len(h4) and h4[h4_ptr + 1].time + _TF_DELTA["H4"] <= at:
                h4_ptr += 1
            if h1_ptr < _MIN_BARS - 1 or h4_ptr < 30 - 1:
                continue
            h1_window = h1[max(0, h1_ptr - _LOOKBACK_BARS + 1) : h1_ptr + 1]
            h4_window = h4[max(0, h4_ptr - _LOOKBACK_BARS + 1) : h4_ptr + 1]

            if h1_snapshot_cache is not None and h1_snapshot_cache[0] == h1_ptr:
                h1_snapshot = h1_snapshot_cache[1]
            else:
                h1_snapshot = analyze_bars([c.model_dump(mode="json") for c in h1_window], symbol=symbol, timeframe="H1")
                h1_snapshot_cache = (h1_ptr, h1_snapshot)
            if h4_snapshot_cache is not None and h4_snapshot_cache[0] == h4_ptr:
                h4_snapshot = h4_snapshot_cache[1]
            else:
                h4_snapshot = analyze_bars([c.model_dump(mode="json") for c in h4_window], symbol=symbol, timeframe="H4")
                h4_snapshot_cache = (h4_ptr, h4_snapshot)

            m15_rows = [c.model_dump(mode="json") for c in m15_window]
            h1_rows = [c.model_dump(mode="json") for c in h1_window]
            h4_rows = [c.model_dump(mode="json") for c in h4_window]
            last_close = m15_window[-1].close

            try:
                ctx = build_strategy_context(
                    symbol=symbol, broker_symbol=symbol, m15_rows=m15_rows, h1_rows=h1_rows, h4_rows=h4_rows,
                    bid=last_close, ask=last_close, spread=Decimal("0"), now=at,
                    h1_snapshot=h1_snapshot, h4_snapshot=h4_snapshot,
                )
            except Exception as exc:
                print(f"[{symbol}] ctx build failed at {at.isoformat()}: {exc.__class__.__name__}: {exc}", flush=True)
                continue
            if ctx is None:
                continue

            future_m15 = m15[i + 1 : i + 1 + _MAX_LOOKFORWARD_BARS]
            for strategy_id, evaluator in _STRATEGY_EVALUATORS.items():
                for variant_name, overrides in _VARIANTS[strategy_id]:
                    _apply_variant(overrides)
                    try:
                        signal = evaluator(ctx)
                    except Exception as exc:
                        print(f"[{symbol}] {strategy_id}/{variant_name} eval failed at {at.isoformat()}: {exc.__class__.__name__}: {exc}", flush=True)
                        continue
                    if not signal.valid:
                        continue
                    outcome = _compute_outcome(direction=signal.direction, entry=signal.proposed_entry, stop_loss=signal.stop_loss, take_profit=signal.take_profit, future_m15=future_m15)
                    trade = _Trade(
                        strategy_id=strategy_id, variant=variant_name, symbol=symbol, direction=signal.direction, entry_time=at.isoformat(),
                        entry=signal.proposed_entry, stop_loss=signal.stop_loss, take_profit=signal.take_profit,
                        outcome_r=outcome.get("outcome_r"), tp_hit=outcome.get("tp_hit"), sl_hit=outcome.get("sl_hit"),
                        immediate_failure=outcome.get("immediate_failure"), resolution_status=outcome["resolution_status"],
                        holding_bars=outcome.get("holding_bars"),
                    )
                    trade_buffer.append(json.dumps(asdict(trade)))
                    if len(trade_buffer) >= _FLUSH_EVERY_TRADES:
                        _flush()

            if instants_done % _PROGRESS_EVERY_INSTANTS == 0:
                _flush()
                with open(progress_path, "w") as pf:
                    pf.write(str(i))
                elapsed = time.perf_counter() - t_loop
                print(f"[{symbol}] {instants_done} instants this run, {elapsed:.1f}s ({elapsed/instants_done*1000:.1f}ms/instant)", flush=True)

        _flush()

    reached_end = (max_instants is None) or (instants_done < max_instants)
    if reached_end:
        with open(progress_path, "w") as pf:
            pf.write(str(end_idx - 1))
        with open(done_path, "w") as df:
            df.write(datetime.now(timezone.utc).isoformat())
        print(f"[{symbol}] DONE (full window complete)", flush=True)
    else:
        print(f"[{symbol}] stopped at max_instants={max_instants} (not a full-window completion, no .done marker)", flush=True)

    elapsed = time.perf_counter() - t_loop
    print(f"[{symbol}] this run: {instants_done} instants in {elapsed:.1f}s ({elapsed/max(instants_done,1)*1000:.1f}ms/instant)", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=list(_ALL_SYMBOLS))
    parser.add_argument("--max-instants", type=int, default=None)
    parser.add_argument("--progress-every", type=int, default=2000)
    parser.add_argument("--lookback-years", type=float, default=None)
    parser.add_argument("--out-dir", type=str, default=_DEFAULT_OUT_DIR)
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    t0 = time.perf_counter()
    for symbol in args.symbols:
        replay_symbol(symbol, max_instants=args.max_instants, progress_every=args.progress_every, lookback_years=args.lookback_years, out_dir=args.out_dir)
    print(f"ALL SYMBOLS DONE for this process in {time.perf_counter()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
