"""Shared math, geometry, stop-loss construction, and evidence-payload builders for every
strategy family evaluator. Extracted verbatim from the original mt5_strategies/families.py
monolith (2026-08-20 restructure) -- the functions below are behavior-identical to their
pre-split versions; nothing in this file changes what any existing strategy computes.

New in this file (2026-08-20 architecture blueprint, Sections 3.3/3.4/3.6): small, genuinely
reusable helpers for the flagged breakout/trend_pullback/smc_continuation levers -- consolidation
scoring, OTE zone math, anti-CHoCH/MSS gating, reaction-candle confirmation, and inducement-sweep
preconditions. Each is a pure function of StrategyContext, used only by the strategy that needs
it today, but written generically enough that a future strategy could reuse the same primitive
rather than re-implementing it -- the exact problem this file's ORIGINAL helpers were extracted
to solve for stop construction.
"""
from __future__ import annotations

import os
import statistics
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pandas as pd

from backend.adaptive_management.tp_protection import construct_dynamic_stop
from backend.brokers.mt5.take_profit import select_take_profit
from backend.market_structure.models import LiquiditySide, StructureBreakKind
from backend.mt5_strategies.context import REGIME_CHOP_RANGING, REGIME_HIGH_VOLATILITY_EXPANSION, REGIME_QUIET_COMPRESSION, REGIME_TRENDING_STRONG, StrategyContext
from backend.mt5_strategies.models import StrategySignal, invalid_signal

_MIN_REWARD_MULTIPLE = Decimal("1.5")


# --------------------------------------------------------------------- env helpers (new) ---
def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------- original shared helpers ---
def _closes(rows: list[dict[str, Any]]) -> pd.Series:
    return pd.Series([float(r["close"]) for r in rows])


def _bounded_rr(entry: Decimal, stop: Decimal, target: Decimal) -> Decimal | None:
    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        return None
    return abs(target - entry) / stop_distance


def _validate_geometry(direction: str, entry: Decimal, stop: Decimal, target: Decimal) -> str | None:
    """Part 10/20: explicit long/short symmetry check -- for a LONG the stop must sit strictly
    below entry and the target strictly above it (mirrored for SHORT). Every strategy's own
    stop/target arithmetic already produces this correctly today; this is defense-in-depth so a
    future bug in one strategy's geometry can never silently reach fusion/execution with
    backwards-sided prices (_bounded_rr's abs() would otherwise happily compute a positive-
    looking ratio for a geometrically inverted trade)."""
    if direction == "LONG":
        if not (stop < entry):
            return "INVALID_STOP_DIRECTION"
        if not (target > entry):
            return "INVALID_TARGET_DIRECTION"
    elif direction == "SHORT":
        if not (stop > entry):
            return "INVALID_STOP_DIRECTION"
        if not (target < entry):
            return "INVALID_TARGET_DIRECTION"
    else:
        return "INVALID_STOP_DIRECTION"
    return None


def _signal(ctx: StrategyContext, *, strategy_id: str, family: str, timeframe: str, direction: str, strength: float,
            entry: Decimal, stop: Decimal, target: Decimal, evidence: dict[str, Any], metadata: dict[str, Any] | None = None) -> StrategySignal:
    """Part 7/9: reward:risk is ALWAYS computed from the FINAL entry/stop/target passed in here
    -- every caller in this module constructs `stop` via _dynamic_stop (already ATR/spread/
    broker-floor-normalized) and `target` from that same final stop's underlying ATR BEFORE
    calling this function, so there is no "provisional stop, provisional RR" step anywhere to
    accidentally retain. `_MIN_REWARD_MULTIPLE` (1.5, unchanged) is evaluated against this final
    RR only."""
    geometry_reason = _validate_geometry(direction, entry, stop, target)
    rr = _bounded_rr(entry, stop, target) if geometry_reason is None else None
    if geometry_reason is not None:
        reason = geometry_reason
    elif rr is None:
        reason = "STOP_DISTANCE_INVALID"
    elif rr < _MIN_REWARD_MULTIPLE:
        reason = "FINAL_RR_BELOW_MINIMUM"
    else:
        reason = None
    valid = reason is None
    # signal_freshness (backend/brokers/mt5/confidence.py) needs the REAL market-data candle
    # time, not generated_at (context-build wall-clock time -- ctx.generated_at is correctly
    # "now" for its own documented purpose, e.g. stale_exit_deadline, but fusion.py previously
    # reused it as the candidate's context["timestamp"] too, which made every fused/non-mtfai1
    # strategy's freshness score trivially ~100 regardless of how stale the underlying M15
    # candle actually was -- e.g. during a scheduler catch-up burst after an outage, when
    # ctx.generated_at is close to "now" even though the candle being evaluated closed hours
    # earlier). mtfai1's own inline context (autonomous.py::_screen) already does this
    # correctly by reading the candle's own timestamp directly; this carries the same real
    # value through StrategySignal.metadata so fusion.py can use it too.
    signal_metadata = dict(metadata or {})
    if ctx.m15_rows:
        signal_metadata.setdefault("candle_time", ctx.m15_rows[-1].get("time"))
    return StrategySignal(
        strategy_id=strategy_id, strategy_family=family, symbol=ctx.symbol, broker_symbol=ctx.broker_symbol,
        direction=direction, timeframe=timeframe, generated_at=ctx.generated_at, valid=valid,
        raw_signal_strength=strength, proposed_entry=float(entry), stop_loss=float(stop), take_profit=float(target),
        reward_risk=float(rr) if rr is not None else None, regime=ctx.regime, evidence=evidence,
        rejection_reason=reason,
        metadata=signal_metadata,
    )


def _no_signal(ctx: StrategyContext, *, strategy_id: str, family: str, timeframe: str, reason: str) -> StrategySignal:
    return invalid_signal(strategy_id, family, symbol=ctx.symbol, broker_symbol=ctx.broker_symbol, timeframe=timeframe, generated_at=ctx.generated_at, regime=ctx.regime, reason=reason)


def _dynamic_stop(
    ctx: StrategyContext, direction: str, entry: Decimal, structure_level: Decimal | float | None, atr: Decimal,
    *, min_atr_mult: float = 1.0, max_atr_mult: float = 3.0,
) -> tuple[Decimal | None, str]:
    """THE one canonical stop-construction path for every strategy in this package (Part 3 --
    "avoid 11 strategies independently calculating fragile stop geometry"). Bounds a proposed
    stop -- either a raw structural reference (a broken BOS level, a swept liquidity price, a
    session high/low) or a pure ATR multiple when no structural level applies -- between
    min_atr_mult x ATR and max_atr_mult x ATR, with a spread-aware buffer AND the broker's own
    minimum stop distance (ctx.broker_min_stop_distance, when known). Reuses the exact same
    utility MTFAI1's own stop construction already relies on (backend.adaptive_management.
    tp_protection.construct_dynamic_stop) rather than inventing a second stop engine.

    NON-NEGOTIABLE: this function's math is UNCHANGED by the 2026-08-20 restructure. Every new
    flagged lever in breakout.py/trend_pullback.py/smc_continuation.py adds a PRECONDITION on
    whether a signal fires at all, never a change to how that signal's stop/target distance is
    computed once it does -- see the architecture blueprint's Section 4 non-negotiables.

    Returns (None, reason) -- never the raw, unbounded level -- when no safe distance can be
    constructed; the reason is one of the explicit rejection codes (Part 10), diagnosed by
    mirroring construct_dynamic_stop's own checks."""
    spread_value = float(ctx.spread) if ctx.spread is not None else 0.0
    atr_f = float(atr) if atr else None
    broker_floor = float(ctx.broker_min_stop_distance) if ctx.broker_min_stop_distance else None
    stop_price = construct_dynamic_stop(
        direction, float(entry), float(structure_level) if structure_level is not None else None,
        atr_f, spread_value, min_atr_mult=min_atr_mult, max_atr_mult=max_atr_mult, broker_min_stop_distance=broker_floor,
    )
    if stop_price is not None:
        return Decimal(str(stop_price)), ""
    if atr_f is None or atr_f <= 0:
        return None, "STOP_BELOW_ATR_FLOOR"
    if structure_level is not None and abs(float(entry) - float(structure_level)) <= 0:
        return None, "INVALID_STOP_DIRECTION"
    min_distance = atr_f * min_atr_mult
    if broker_floor is not None:
        min_distance = max(min_distance, broker_floor)
    max_distance = atr_f * max_atr_mult
    if min_distance > max_distance:
        return None, "STOP_DISTANCE_INVALID"
    if broker_floor is not None and min_distance < broker_floor:
        return None, "BROKER_STOP_LEVEL_VIOLATION"
    if min_distance < spread_value * 3.0:
        return None, "STOP_INSIDE_SPREAD_BUFFER"
    return None, "STOP_DISTANCE_INVALID"


def _eqh_eql_touch_count(ctx: StrategyContext, *, side: str, price: float, atr: float) -> int:
    """Touch count of the nearest ACTIVE (not yet swept, within this strategy's own visible
    ~100-bar window) EQH/EQL liquidity pool of `side` within tolerance of `price`, or 0 if none
    -- market_structure/liquidity.py::detect_equal_levels' 2026-08-17 audit addition. This is a
    CONFIRMATION-ONLY signal: real EURUSD chronological OOS validation (docs/
    EXTERNAL_INDICATOR_REDUNDANCY_AUDIT.md addendum) showed a consistent, same-sign, OOS-STABLE
    positive effect for support_resistance_bounce (n=640, +0.12R train -> +0.24R OOS) and
    smc_continuation (n=108, +0.17R train -> +0.36R OOS) specifically -- those two callers use
    this to add a bounded strength bonus. breakout and liquidity_sweep_reversal showed either
    sign-unstable or too-small-to-trust results on the same corpus -- those callers record this
    in `evidence` for observability only and must NEVER let it change strength/entry/stop/target.
    Never gates any strategy's own trigger condition (called only after a signal already exists)."""
    swept_ids = {s.level_id for s in ctx.m15_snapshot.equal_level_sweeps}
    candidates = [lvl for lvl in ctx.m15_snapshot.equal_levels if lvl.side == side and lvl.id not in swept_ids]
    if not candidates:
        return 0
    nearest = min(candidates, key=lambda lv: abs(float(lv.level) - price))
    tolerance = max(float(nearest.tolerance), atr)
    if abs(float(nearest.level) - price) > tolerance:
        return 0
    return nearest.touch_count


def _squeeze_evidence(ctx: StrategyContext) -> dict[str, Any]:
    """Observability-only squeeze/momentum evidence (LazyBear Squeeze Momentum, market_structure/
    squeeze_momentum.py) -- attached to relevant strategies' evidence dict for future analysis.
    2026-08-17 chronological OOS validation was NEGATIVE across all four tested roles (standalone,
    confirmation-filter, HI fingerprint, hybrid -- see the [Stage 2] commit): squeeze_state's
    apparent predictive value did not survive a chronological split (sign-flipped train vs OOS).
    This function exists SOLELY so the raw values are visible in the evidence trail; it must NEVER
    be used to adjust strength, gate a signal, or otherwise influence a trading decision -- doing
    so would contradict the validation's own negative finding. The Pivot Trendline Module
    (market_structure/pivot_trendlines.py) is deliberately held to this exact same bar before any
    strategy is allowed to read it live."""
    return {"squeeze_state": ctx.squeeze_state, "squeeze_momentum_value": ctx.squeeze_momentum_value}


def _geometry_metadata(ctx: StrategyContext, entry: Decimal, stop: Decimal, structure_level: Decimal | float | None, atr: Decimal, min_atr_mult: float, max_atr_mult: float) -> dict[str, Any]:
    """Part 11/19 geometry audit trail, attached to every valid signal's `metadata` -- makes
    "why did this trade use this stop" answerable from persisted data without tracing code."""
    return {
        "structural_reference": float(structure_level) if structure_level is not None else None,
        "atr": float(atr) if atr else None,
        "spread": float(ctx.spread) if ctx.spread is not None else None,
        "broker_min_stop_distance": float(ctx.broker_min_stop_distance) if ctx.broker_min_stop_distance else None,
        "final_stop_distance": float(abs(entry - stop)),
        "min_atr_mult": min_atr_mult,
        "max_atr_mult": max_atr_mult,
    }


# ------------------------------------------------------------ new shared helpers (2026-08-20) ---
def _consolidation_quality_score(ctx: StrategyContext, *, reference_level: Decimal | float, atr: float, max_lookback_bars: int = 20) -> float:
    """0-100 score describing the range price consolidated in immediately before a breakout --
    breakout.py's Section 3.3 lever. Three bounded components, weighted equally: (1) how many of
    the last `max_lookback_bars` bars stayed inside a tight band around `reference_level`
    (longer, tighter consolidation = higher-conviction breakout), (2) how narrow that band is
    relative to ATR (a range far wider than ATR is not a real consolidation), (3) how many times
    price touched the boundary of that band (repeated tests = more participants aware of the
    level). Pure evidence -- see breakout.py for how (and whether) this is allowed to affect a
    decision."""
    if atr <= 0 or not ctx.m15_rows:
        return 0.0
    level = float(reference_level)
    window = ctx.m15_rows[-max_lookback_bars:]
    if len(window) < 5:
        return 0.0
    band = atr * 1.5
    inside = [r for r in window if abs(float(r["close"]) - level) <= band]
    duration_score = min(100.0, (len(inside) / len(window)) * 100.0)
    band_low, band_high = level - band, level + band
    range_span = max((float(r["high"]) for r in window), default=level) - min((float(r["low"]) for r in window), default=level)
    tightness_score = max(0.0, 100.0 - (range_span / atr) * 20.0) if range_span > 0 else 100.0
    touches = sum(1 for r in window if float(r["high"]) >= band_high - atr * 0.25 or float(r["low"]) <= band_low + atr * 0.25)
    touch_score = min(100.0, touches * 15.0)
    return round((duration_score + tightness_score + touch_score) / 3.0, 1)


def _ote_zone_for_direction(ctx: StrategyContext, direction: str) -> tuple[float, float] | None:
    """Optimal Trade Entry zone: the 61.8%-78.6% Fibonacci retracement of the most recent
    completed impulse leg in `direction`'s favor, computed from swings already present in
    ctx.m15_snapshot.swings (no new swing detection). LONG: most recent swing low, then the
    first swing high confirmed after it (the up-leg); zone is the retracement BELOW that high.
    SHORT: mirrored. Returns None when no such leg exists in the current M15 window -- trend_
    pullback.py treats that as "confluence not available", never as a synthetic pass."""
    swings = sorted(ctx.m15_snapshot.swings, key=lambda s: s.bar_index)
    if direction == "LONG":
        lows = [s for s in swings if s.swing_type == "low"]
        if not lows:
            return None
        leg_low = lows[-1]
        highs_after = [s for s in swings if s.swing_type == "high" and s.bar_index > leg_low.bar_index]
        if not highs_after:
            return None
        leg_high = highs_after[-1]
        low_price, high_price = float(leg_low.price), float(leg_high.price)
        leg = high_price - low_price
        if leg <= 0:
            return None
        return (high_price - leg * 0.786, high_price - leg * 0.618)
    if direction == "SHORT":
        highs = [s for s in swings if s.swing_type == "high"]
        if not highs:
            return None
        leg_high = highs[-1]
        lows_after = [s for s in swings if s.swing_type == "low" and s.bar_index > leg_high.bar_index]
        if not lows_after:
            return None
        leg_low = lows_after[-1]
        high_price, low_price = float(leg_high.price), float(leg_low.price)
        leg = high_price - low_price
        if leg <= 0:
            return None
        return (low_price + leg * 0.618, low_price + leg * 0.786)
    return None


def _recent_structure_break_against(ctx: StrategyContext, direction: str, *, lookback_bars: int) -> bool:
    """True if a CHoCH or MSS AGAINST `direction` exists in the last `lookback_bars` M15 bars --
    trend_pullback.py's anti-CHoCH/MSS gate (Section 3.4). A pullback taken right after the
    market's own character just changed against it is exactly the false-continuation setup the
    strategy's real 8-year audit (57.4% immediate-failure rate on the LONG side, 2018-2026) looks
    like. Reuses ctx.m15_snapshot.breaks -- the same field liquidity_sweep_reversal.py already
    reads for its own (untouched) sequencing logic -- no new detector."""
    if not ctx.m15_rows:
        return False
    against_kind = {StructureBreakKind.CHOCH.value, StructureBreakKind.MSS.value}
    against_direction = "bearish" if direction == "LONG" else "bullish"
    recent_window = max(0, len(ctx.m15_rows) - 1 - lookback_bars)
    return any(
        b.break_kind in against_kind and b.direction == against_direction and b.bar_index >= recent_window
        for b in ctx.m15_snapshot.breaks
    )


def _reaction_candle_confirms(ctx: StrategyContext, direction: str) -> bool:
    """Same rejection-candle pattern support_resistance_bounce.py already uses (bullish close-
    over-open for LONG, bearish close-under-open for SHORT), generalized here so trend_pullback.py
    can require it too (Section 3.4) without a second implementation. Checks only the most recent
    bar -- a reaction candle is by definition the LATEST bar's behavior, not a lookback window."""
    if not ctx.m15_rows:
        return False
    last = ctx.m15_rows[-1]
    close, open_ = float(last["close"]), float(last["open"])
    if direction == "LONG":
        return close > open_
    if direction == "SHORT":
        return close < open_
    return False


def _liquidity_sweep_precedes(ctx: StrategyContext, *, before_bar_index: int, trend_direction: str) -> bool:
    """Inducement (IDM) precondition: was there a liquidity sweep, in the side that would trap
    counter-trend participants, before `before_bar_index` (the triggering BOS's own bar) --
    smc_continuation.py's Section 3.6 lever. For a bullish continuation the trapping sweep is a
    SELL-side sweep (stops of short-sellers/late longs run out before the real move up); mirrored
    for bearish. Reuses ctx.m15_snapshot.liquidity_sweeps -- the exact same field
    liquidity_sweep_reversal.py's own (untouched) sequencing already reads -- this is new
    COMPOSITION of existing data, not a new detector, and never touches that strategy's logic."""
    trapping_side = "sell_side" if trend_direction == "bullish" else "buy_side"
    return any(s.side == trapping_side and s.bar_index < before_bar_index for s in ctx.m15_snapshot.liquidity_sweeps)


# ================================================================================================
# Phase 2 (2026-08-21 blueprint): engine-wide ADX regime gate, session-liquidity timing filter,
# stale-trade exit metadata, and spread safety buffer. Every gate below is FAIL-OPEN by
# construction: when its feature flag is off (the default for all four), or when the underlying
# data needed to evaluate it isn't available, the gate returns "permitted"/"no adjustment" -- a
# Phase 2 computation failure or an unset flag must never be able to block or alter a trade that
# Phase 1 behavior would have allowed. None of this changes _dynamic_stop's formula (Section 5's
# non-negotiable) -- the volatility-expansion lever only widens the max_atr_mult INPUT passed into
# that same, unchanged function.
# ================================================================================================

# ------------------------------------------------------------- 1. ADX regime gate/adjustments ---
# Per-regime strategy eligibility (Phase 2 Section 1) -- disables are the only HARD gates; the
# "Boost mean_reversion" / "Enable trend_pullback, smc_continuation, breakout" language in the
# spec is otherwise either a strength bonus (boost) or simply "this strategy is unaffected in this
# regime" (enable) -- momentum and session_breakout are named in the spec's CHOP_RANGING disable
# list too, but their evaluator files were not part of this deliverable's requested output list
# (still living in families/_legacy.py, unmodified) -- the tables below already key correctly on
# their strategy_id so wiring them in later is a one-line addition to that file, not a redesign.
_REGIME_DISABLED_STRATEGIES: dict[str, frozenset[str]] = {
    REGIME_CHOP_RANGING: frozenset({"breakout", "ema_trend", "momentum"}),
    REGIME_TRENDING_STRONG: frozenset({"mean_reversion"}),
}
_REGIME_BOOSTED_STRATEGIES: dict[str, frozenset[str]] = {
    REGIME_CHOP_RANGING: frozenset({"mean_reversion"}),
}
_REGIME_STRENGTH_BONUS = 10.0


def _regime_strategy_disabled(ctx: StrategyContext, strategy_id: str) -> bool:
    """True only when MT5_REGIME_FILTER_ENABLED is on AND the current market_regime's disable
    table names this strategy -- fail-open (False) otherwise, including when market_regime is
    NEUTRAL (ADX in the 20-25 gap, or ADX unavailable) since NEUTRAL carries no disable table."""
    if not _env_flag("MT5_REGIME_FILTER_ENABLED", False):
        return False
    return strategy_id in _REGIME_DISABLED_STRATEGIES.get(ctx.market_regime, frozenset())


def _regime_strength_bonus(ctx: StrategyContext, strategy_id: str) -> float:
    """Additive strength bonus (0 when the flag is off, the regime doesn't boost this strategy,
    or gating is disabled) -- mean_reversion in CHOP_RANGING per Phase 2 Section 1. Same bounded-
    additive pattern as _eqh_eql_touch_count's validated bonus, never a multiplier."""
    if not _env_flag("MT5_REGIME_FILTER_ENABLED", False):
        return 0.0
    if strategy_id in _REGIME_BOOSTED_STRATEGIES.get(ctx.market_regime, frozenset()):
        return _REGIME_STRENGTH_BONUS
    return 0.0


def _regime_atr_mult_scale(ctx: StrategyContext) -> float:
    """HIGH_VOLATILITY_EXPANSION's "force wider ATR buffers" -- returns a multiplier (>1.0) to
    apply to a strategy's own max_atr_mult argument to _dynamic_stop, never a change to
    _dynamic_stop's formula itself. Returns 1.0 (no adjustment) when the flag is off or the
    regime isn't HIGH_VOLATILITY_EXPANSION -- callers do `max_atr_mult * _regime_atr_mult_scale
    (ctx)` and pass that through unchanged otherwise."""
    if not _env_flag("MT5_REGIME_FILTER_ENABLED", False):
        return 1.0
    if ctx.market_regime != REGIME_HIGH_VOLATILITY_EXPANSION:
        return 1.0
    return _env_float("MT5_HIGH_VOL_ATR_WIDEN_MULT", 1.3)


def _regime_prefers_breakout_retest(ctx: StrategyContext) -> bool:
    """QUIET_COMPRESSION "primes breakout retest mode" -- True only when the flag is on AND the
    current regime is QUIET_COMPRESSION; breakout.py uses this to prefer RETEST_AND_HOLD even when
    MT5_BREAKOUT_ENTRY_MODE isn't explicitly set to it, since a tight pre-breakout range is exactly
    the setup a retest-and-hold confirmation was designed for. Never overrides an EXPLICIT
    MT5_BREAKOUT_ENTRY_MODE=BREAK_AND_GO -- see breakout.py's own dispatch for how this is applied
    only when the operator hasn't already made an explicit choice."""
    if not _env_flag("MT5_REGIME_FILTER_ENABLED", False):
        return False
    return ctx.market_regime == REGIME_QUIET_COMPRESSION


# --------------------------------------------------------- 2. session liquidity timing filter ---
_LONDON_WINDOW = (7, 16)   # 07:00-16:00 UTC, half-open
_NY_WINDOW = (12, 17)      # 12:00-17:00 UTC, half-open
_ROLLOVER_START_HOUR = 21  # 21:00 UTC
_ROLLOVER_END_HOUR = 1     # 01:00 UTC (wraps past midnight)
_ASIAN_SPECIALIZED_CODES = ("AUD", "NZD", "JPY")
_SESSION_RESTRICTED_STRATEGIES = frozenset({"breakout", "session_breakout", "momentum"})


def is_session_liquidity_valid(symbol: str, strategy_name: str, current_time_utc: datetime) -> bool:
    """Phase 2 Section 2: is `current_time_utc` (any tz-aware or naive UTC datetime) inside a
    peak-liquidity window for `symbol`/`strategy_name`. Only meaningful for the high-slippage
    strategies this restricts (breakout, session_breakout, momentum) -- returns True unconditionally
    for every other strategy_name, since the spec scopes this restriction to exactly those three.

    Peak windows, all UTC: London Open & Overlap 07:00-16:00, New York Peak 12:00-17:00 (these
    overlap 12:00-16:00, combining to one continuous 07:00-17:00 peak block). The 21:00-01:00
    rollover window is explicitly BLOCKED for every symbol EXCEPT AUD/NZD/JPY pairs (substring
    match on the symbol, e.g. "AUDUSD"/"NZDJPY"/"USDJPY" all qualify), for whom that window IS
    their own specialized Asian session and is therefore treated as valid, not blocked. Every
    other hour (01:00-07:00, 17:00-21:00) is outside both the peak windows and the Asian-pair
    carve-out, so it is invalid for the 3 restricted strategies -- the spec's "strictly to peak
    liquidity windows" phrasing is read as exhaustive, not merely emphasizing the rollover case.

    Pure and unconditional -- does NOT itself check MT5_SESSION_TIMING_FILTER_ENABLED; see
    _session_timing_permits for the flag-gated wrapper every strategy evaluator actually calls."""
    if strategy_name not in _SESSION_RESTRICTED_STRATEGIES:
        return True
    hour = current_time_utc.hour
    in_london = _LONDON_WINDOW[0] <= hour < _LONDON_WINDOW[1]
    in_ny = _NY_WINDOW[0] <= hour < _NY_WINDOW[1]
    in_rollover = hour >= _ROLLOVER_START_HOUR or hour < _ROLLOVER_END_HOUR
    is_asian_specialized = any(code in symbol.upper() for code in _ASIAN_SPECIALIZED_CODES)
    if in_rollover:
        return is_asian_specialized
    return in_london or in_ny


def _session_timing_permits(ctx: StrategyContext, strategy_id: str) -> bool:
    """Flag-gated wrapper every evaluator actually calls -- fail-open (True) when
    MT5_SESSION_TIMING_FILTER_ENABLED is off, matching every other Phase 2 gate's contract."""
    if not _env_flag("MT5_SESSION_TIMING_FILTER_ENABLED", False):
        return True
    return is_session_liquidity_valid(ctx.broker_symbol, strategy_id, ctx.generated_at)


# ------------------------------------------------------------- 3. time-based stale-trade exit ---
def _stale_exit_metadata(ctx: StrategyContext, *, strategy_id: str) -> dict[str, Any]:
    """Phase 2 Section 3: computes (never enforces -- there is no live position to manage inside
    a stateless evaluator) the stale-exit PARAMETERS attached to a signal's metadata payload, for
    downstream trade management (adaptive_management's own position monitor) to apply once a
    trade is open. `stale_exit_applies` is the single key downstream code needs to check --
    True only when the flag is on AND this strategy is one of the two the spec scopes this to
    (breakout, trend_pullback); every other strategy still gets this key (False) rather than a
    missing key, so downstream code never needs a strategy-id special case of its own.
    `stale_exit_deadline` is a convenience wall-clock estimate (ctx.generated_at + N M15 bars),
    computed only when the rule applies -- the authoritative check downstream must still be BAR
    count, not wall-clock time, since a market can halt/gap across the deadline."""
    enabled = _env_flag("MT5_STALE_EXIT_ENABLED", False)
    n_bars = _env_int("MT5_STALE_EXIT_BARS", 8)
    min_r = _env_float("MT5_STALE_EXIT_MIN_R", 0.5)
    applies = enabled and strategy_id in {"breakout", "trend_pullback"}
    return {
        "stale_exit_enabled": enabled,
        "stale_exit_applies": applies,
        "stale_exit_bars": n_bars,
        "stale_exit_min_r": min_r,
        "stale_exit_deadline": (ctx.generated_at + timedelta(minutes=15 * n_bars)).isoformat() if applies else None,
    }


# ------------------------------------------------------------ 4. spread/volatility safety rail ---
def _spread_within_safety_buffer(ctx: StrategyContext, *, lookback_bars: int = 50, min_samples: int = 10) -> bool:
    """Phase 2 Section 4: current_spread <= MT5_MAX_SPREAD_MULT x rolling_median_spread. Fail-open
    (True) when the flag is off, when fewer than `min_samples` recent bars carry a usable spread
    reading (rollover/illiquid gaps in the feed itself must never be read as "spread is fine",
    but they must also never be able to BLOCK trading by starving this check of data -- fail-open
    resolves that in favor of Phase 1's unchanged behavior), or when the resulting median is zero
    or negative (a data quality issue, not a real zero-spread market).

    UNIT MISMATCH, handled explicitly: `ctx.spread` (the live quote) is ask-bid in PRICE units
    (backend/brokers/mt5/quotes.py::quote_from_tick -- e.g. 0.00012 for a EURUSD tick), but each
    M15 row's own `spread` field is MT5's raw per-bar integer POINT count
    (backend/brokers/mt5/models.py::MT5Candle.spread -- e.g. 12), typically 4-5 orders of
    magnitude apart. Comparing them directly would make `current_spread <= mult * median_spread`
    trivially true almost always, silently disabling the filter even with the flag on. An earlier
    draft of this function tried to self-calibrate a points-to-price conversion factor from the
    SAME cycle's live spread reading -- wrong, because that reading is also the value being
    tested: an abnormally wide current spread would inflate its own calibration factor by the
    same proportion, canceling out and always passing regardless of how wide the spread actually
    was. Fixed properly: uses `ctx.point_value` (symbol_info.point, in price units, populated by
    build_strategy_context exactly like broker_min_stop_distance) as the conversion factor -- a
    fixed, non-circular, per-symbol constant. Fails open (True) whenever point_value is
    unavailable (symbol_info wasn't supplied, e.g. most replay/backtest contexts) -- never guessed,
    same "never guessed" contract broker_min_stop_distance already holds itself to."""
    if not _env_flag("MT5_SPREAD_FILTER_ENABLED", False):
        return True
    if ctx.point_value is None or ctx.point_value <= 0:
        return True
    point_value = float(ctx.point_value)
    window = ctx.m15_rows[-lookback_bars:]
    point_spreads = [float(r["spread"]) for r in window if r.get("spread") is not None and float(r["spread"]) > 0]
    if len(point_spreads) < min_samples:
        return True
    median_price_spread = statistics.median(point_spreads) * point_value
    if median_price_spread <= 0:
        return True
    mult = _env_float("MT5_MAX_SPREAD_MULT", 1.5)
    return float(ctx.spread) <= mult * median_price_spread


# ================================================================================================
# Phase 3 (2026-08-21 blueprint, Section 1): engine-wide structural take-profit propagation.
# mtfai1's own scoring (backend/brokers/mt5/autonomous.py::_score_candidate) already targets the
# real opposing swing level via backend.brokers.mt5.take_profit.select_take_profit instead of a
# flat ATR multiple -- the 8-year audit named this mtfai1's "gold standard" and the single highest-
# leverage change for the rest of the engine. select_take_profit itself already existed as a
# standalone, generic function (not embedded in mtfai1's own code) -- nothing needed extracting;
# what was missing was a shared way to FIND the opposing structural level from a StrategyContext,
# which is what _opposing_structural_level below provides. Gated engine-wide behind
# MT5_STRUCTURAL_TP_ENABLED (default False); every caller falls back to its own existing flat-ATR-
# multiple target, unchanged, when the flag is off or no structural level is found.
# ================================================================================================
def _opposing_structural_level(ctx: StrategyContext, direction: str) -> Decimal | None:
    """Nearest OPPOSING unmitigated structural reference ahead of price in `direction`'s own
    favor -- the target a LONG would travel UP to reach, mirrored for SHORT. Same "ahead of price,
    trade's own direction" convention already established by
    backend/brokers/mt5/autonomous.py::_mtfai1_equal_level_clear (BUY_SIDE liquidity is ahead of a
    LONG, SELL_SIDE ahead of a SHORT) and by _eqh_eql_touch_count's callers, just repurposed here
    for target selection instead of an entry-blocking check.

    Checked in order, first non-empty source wins (closest realistic target first, matching
    select_take_profit's own "nearest candidate that still clears the reward floor" philosophy --
    this function only supplies candidates, select_take_profit does the actual floor/ceiling
    bounding):
      1. Active (unswept) EQH/EQL equal-level pools on the ahead side (ctx.m15_snapshot.
         equal_levels) -- the most concrete, repeated-touch liquidity concentration available.
      2. Swing-derived LiquidityLevel pools on the ahead side (ctx.m15_snapshot.liquidity_levels).
      3. Unmitigated order blocks / FVGs on the OPPOSING trend direction sitting ahead of price --
         an opposing-direction OB/FVG above a LONG (or below a SHORT) is unfilled supply/demand
         price is commonly drawn to test before any real reversal, a standard ICT target concept.
      4. The nearest raw swing high (for LONG) / swing low (for SHORT) ahead of price, from
         ctx.m15_snapshot.swings -- the least specific but always-available fallback.
    Returns None when nothing qualifies in any tier -- select_take_profit's own ATR-projected-move
    (or reward-floor) fallback applies from there; this function never invents a level."""
    if not ctx.m15_rows:
        return None
    price = float(_closes(ctx.m15_rows).iloc[-1])
    ahead_side = LiquiditySide.BUY_SIDE if direction == "LONG" else LiquiditySide.SELL_SIDE
    opposing_trend_direction = "bearish" if direction == "LONG" else "bullish"

    def _ahead(level_price: float) -> bool:
        return (level_price > price) if direction == "LONG" else (level_price < price)

    swept_ids = {s.level_id for s in ctx.m15_snapshot.equal_level_sweeps}
    eqh_eql_candidates = [float(lvl.level) for lvl in ctx.m15_snapshot.equal_levels if lvl.side == ahead_side and lvl.id not in swept_ids and _ahead(float(lvl.level))]
    if eqh_eql_candidates:
        return Decimal(str(min(eqh_eql_candidates, key=lambda level: abs(level - price))))

    liquidity_candidates = [float(lvl.level) for lvl in ctx.m15_snapshot.liquidity_levels if lvl.side == ahead_side and _ahead(float(lvl.level))]
    if liquidity_candidates:
        return Decimal(str(min(liquidity_candidates, key=lambda level: abs(level - price))))

    zone_candidates: list[float] = []
    for ob in ctx.m15_snapshot.order_blocks:
        if ob.direction != opposing_trend_direction or ob.status in {"mitigated", "invalidated"} or ob.price_low is None or ob.price_high is None:
            continue
        edge = float(ob.price_low) if direction == "LONG" else float(ob.price_high)
        if _ahead(edge):
            zone_candidates.append(edge)
    for fvg in ctx.m15_snapshot.imbalances:
        if fvg.direction != opposing_trend_direction or fvg.status == "mitigated" or fvg.price_low is None or fvg.price_high is None:
            continue
        edge = float(fvg.price_low) if direction == "LONG" else float(fvg.price_high)
        if _ahead(edge):
            zone_candidates.append(edge)
    if zone_candidates:
        return Decimal(str(min(zone_candidates, key=lambda level: abs(level - price))))

    swing_type = "high" if direction == "LONG" else "low"
    swing_candidates = [float(s.price) for s in ctx.m15_snapshot.swings if s.swing_type == swing_type and _ahead(float(s.price))]
    if swing_candidates:
        return Decimal(str(min(swing_candidates, key=lambda level: abs(level - price))))
    return None


# ================================================================================================
# mean_reversion/trend_pullback evidence-upgrade, Steps 1-2 (docs/mean-reversion-trend-pullback-
# implementation-spec.md). Observability-only: nothing below is read by either strategy's
# `strength` calculation yet -- these are pure functions of StrategyContext, attached to
# `evidence` dicts only, held to the exact same "compute, don't yet trust" bar as
# displacement_magnitude_atr and _eqh_eql_touch_count were before their own OOS validation.
# ================================================================================================
def _wick_rejection_score(ctx: StrategyContext, direction: str) -> float:
    """0-100: what fraction of the latest M15 bar's high-low range is a rejecting wick on
    `direction`'s side -- lower wick for LONG (rejection of a push lower), upper wick for SHORT.
    Distinct from _reaction_candle_confirms (body-close direction only): this measures HOW MUCH
    of the bar was rejected, not just which way it closed on net. A confirmed gap (spec section
    6) -- no wick/rejection-length detector exists anywhere else in this codebase."""
    if not ctx.m15_rows:
        return 0.0
    last = ctx.m15_rows[-1]
    try:
        o, h, l, c = float(last["open"]), float(last["high"]), float(last["low"]), float(last["close"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    total_range = h - l
    if total_range <= 0:
        return 0.0
    body_low, body_high = min(o, c), max(o, c)
    if direction == "LONG":
        wick = body_low - l
    elif direction == "SHORT":
        wick = h - body_high
    else:
        return 0.0
    return round(max(0.0, min(100.0, (wick / total_range) * 100.0)), 1)


_LOCATION_QUALITY_BY_FACTOR_COUNT: dict[int, float] = {0: 0.0, 1: 12.0, 2: 18.0, 3: 22.0, 4: 24.0}


def _location_quality_score(factor_count: int) -> float:
    """Capped, sub-linear score from how many independent location factors align (spec section
    2's redundancy map): +12/+6/+4/+2 as each additional factor stacks, never a flat sum -- a
    genuinely strong zone that trips every factor at once must not linearly dominate a location
    with only one real factor present."""
    return _LOCATION_QUALITY_BY_FACTOR_COUNT.get(min(max(factor_count, 0), 4), 24.0)


def _zone_overlap(ctx: StrategyContext, *, zone_direction: str, price: float) -> bool:
    """True if `price` sits inside any non-mitigated FVG or active/partial order block whose own
    `direction` equals `zone_direction` -- same fields smc_continuation.py/trend_pullback.py
    already read (m15_snapshot.imbalances/.order_blocks), no new detector. Caller decides what
    `zone_direction` means for its own hypothesis (e.g. mean_reversion passes the OPPOSING
    direction to the move being faded; trend_pullback passes the WITH-trend direction)."""
    for fvg in ctx.m15_snapshot.imbalances:
        if fvg.direction == zone_direction and fvg.status != "mitigated" and fvg.price_low is not None and fvg.price_high is not None:
            if float(fvg.price_low) <= price <= float(fvg.price_high):
                return True
    for ob in ctx.m15_snapshot.order_blocks:
        if ob.direction == zone_direction and ob.status not in {"mitigated", "invalidated"} and ob.price_low is not None and ob.price_high is not None:
            if float(ob.price_low) <= price <= float(ob.price_high):
                return True
    return False


def _premium_discount_position(ctx: StrategyContext) -> float | None:
    """The current close's position within the latest M15 dealing range, 0 (range low/discount)
    to 1 (range high/premium) -- build_dealing_ranges() always produces at most one dealing range
    per snapshot (see market_structure/dealing_range.py), already normalized to the latest close,
    so this is a direct read, not a new computation. None when no dealing range exists yet
    (insufficient swing history). Computed but consumed by ZERO strategies today (confirmed gap,
    spec section 1/6) -- this is the first reader, observability-only."""
    ranges = ctx.m15_snapshot.dealing_ranges
    if not ranges:
        return None
    return ranges[0].normalized_current_position


def _nearest_liquidity_level_atr_distance(ctx: StrategyContext, *, price: float, atr: float) -> float | None:
    """Distance, in ATR units, from `price` to the nearest swing-derived LiquidityLevel (S/R) of
    either side -- same field support_resistance_bounce.py already reads
    (m15_snapshot.liquidity_levels), no new detector. None when no liquidity level exists or ATR
    is unavailable. Observability/context only -- not a proximity gate."""
    levels = ctx.m15_snapshot.liquidity_levels
    if not levels or atr <= 0:
        return None
    nearest = min(levels, key=lambda lv: abs(float(lv.level) - price))
    return round(abs(float(nearest.level) - price) / atr, 3)


def _location_quality_mean_reversion(ctx: StrategyContext, *, direction: str, price: float, atr: float) -> dict[str, Any]:
    """Capped location-quality composite for a mean_reversion candidate fading toward `direction`
    (spec section 4.2). Factors: EQH/EQL touch on the side being faded, an opposing-direction
    OB/FVG at price (a zone that would resist the move continuing), price sitting at a
    premium/discount extreme (favorable side for the reversal), and proximity to a swing-derived
    liquidity level. Sub-linear (`_location_quality_score`), never a flat sum of the four.
    Observability-only -- see this module's section header."""
    eqh_eql_side = "sell_side" if direction == "LONG" else "buy_side"
    eqh_eql_touches = _eqh_eql_touch_count(ctx, side=eqh_eql_side, price=price, atr=atr)
    opposing_zone_direction = "bearish" if direction == "LONG" else "bullish"
    ob_fvg_overlap = _zone_overlap(ctx, zone_direction=opposing_zone_direction, price=price)
    pd_position = _premium_discount_position(ctx)
    at_pd_extreme = pd_position is not None and ((pd_position <= 0.15) if direction == "LONG" else (pd_position >= 0.85))
    nearest_level_atr = _nearest_liquidity_level_atr_distance(ctx, price=price, atr=atr)
    near_level = nearest_level_atr is not None and nearest_level_atr <= 0.5
    factor_count = sum([eqh_eql_touches >= 1, ob_fvg_overlap, at_pd_extreme, near_level])
    return {
        "eqh_eql_touch_count": eqh_eql_touches,
        "opposing_ob_fvg_overlap": ob_fvg_overlap,
        "premium_discount_position": pd_position,
        "at_premium_discount_extreme": at_pd_extreme,
        "nearest_liquidity_level_atr_distance": nearest_level_atr,
        "location_factor_count": factor_count,
        "location_quality_score": _location_quality_score(factor_count),
    }


def _location_quality_trend_pullback(ctx: StrategyContext, *, direction: str, price: float, atr: float) -> dict[str, Any]:
    """Capped location-quality composite for a trend_pullback candidate continuing `direction`
    (spec section 5.2). Factors: the retracement landing inside the OTE fib zone, a with-trend
    OB/FVG at price, price sitting in discount (LONG) / premium (SHORT) -- i.e. NOT already
    chasing back toward the extreme it just retraced from -- and proximity to a swing-derived
    liquidity level. Sub-linear, never a flat sum. Observability-only."""
    ote = _ote_zone_for_direction(ctx, direction)
    in_ote = ote is not None and ote[0] <= price <= ote[1]
    with_trend_zone_direction = "bullish" if direction == "LONG" else "bearish"
    ob_fvg_overlap = _zone_overlap(ctx, zone_direction=with_trend_zone_direction, price=price)
    pd_position = _premium_discount_position(ctx)
    in_favorable_pd = pd_position is not None and ((pd_position <= 0.5) if direction == "LONG" else (pd_position >= 0.5))
    nearest_level_atr = _nearest_liquidity_level_atr_distance(ctx, price=price, atr=atr)
    near_level = nearest_level_atr is not None and nearest_level_atr <= 0.5
    factor_count = sum([in_ote, ob_fvg_overlap, in_favorable_pd, near_level])
    return {
        "in_ote_zone": in_ote,
        "with_trend_ob_fvg_overlap": ob_fvg_overlap,
        "premium_discount_position": pd_position,
        "in_favorable_premium_discount_side": in_favorable_pd,
        "nearest_liquidity_level_atr_distance": nearest_level_atr,
        "location_factor_count": factor_count,
        "location_quality_score": _location_quality_score(factor_count),
    }


def _structural_take_profit(ctx: StrategyContext, *, direction: str, entry: Decimal, stop: Decimal, atr: Decimal | None) -> dict[str, Any] | None:
    """MT5_STRUCTURAL_TP_ENABLED (default False) wrapper around select_take_profit -- the exact
    function mtfai1's own scoring already calls, reused here rather than reimplemented. Returns
    None when the flag is off (callers must fall back to their own existing flat-ATR-multiple
    target unchanged) or when select_take_profit itself couldn't produce a usable tp1 (invalid
    stop distance). This wrapper only supplies the opposing_structure_level input; it never
    touches select_take_profit's own [1.5, 5.0]x-stop-distance bounding or its ATR-projected-move/
    reward-floor fallback chain -- see that function's own docstring for those rules."""
    if not _env_flag("MT5_STRUCTURAL_TP_ENABLED", False):
        return None
    opposing = _opposing_structural_level(ctx, direction)
    selection = select_take_profit(direction=direction, entry=entry, stop_loss=stop, opposing_structure_level=opposing, atr=atr)
    if selection.get("tp1") is None:
        return None
    return selection
