"""Canonical strategy family implementations for the MT5 multi-strategy layer.

Each function takes a shared, already-computed StrategyContext (Market Data -> features, see
context.py) and returns exactly one StrategySignal. No broker I/O, no IBKR imports, no OpenAI.

Consolidation record (Part 1 -- canonical mapping, duplicates retired in favor of one
implementation per family):
  ema_trend                  <- intelligence/trading/strategies.py::EMATrendStrategy
                                 (deprecates strategies/registry.py::ema_trend_continuation_v1,
                                 a DSL-only duplicate with no independent logic)
  trend_pullback              <- intelligence/trading/strategies.py::PullbackStrategy
                                 (deprecates forex_strategies::trend_pullback_v1, metadata only)
  breakout                     <- intelligence/trading/strategies.py::BreakoutStrategy, adapted
                                 to read backend.market_structure BOS objects directly instead
                                 of that pipeline's own naive support/resistance calc
                                 (deprecates SupportResistanceBreakStrategy, a literal duplicate
                                 of BreakoutStrategy; deprecates forex_strategies::
                                 breakout_retest_v1 and strategies/registry.py::
                                 donchian_breakout_v1, both thinner/metadata-only duplicates)
  mean_reversion                <- intelligence/trading/strategies.py::MeanReversionStrategy
                                 (deprecates forex_frameworks::MeanReversionFramework,
                                 strategies/registry.py::rsi_mean_reversion_v1, forex_strategies
                                 ::mean_reversion_range_v1 -- three thinner duplicates)
  liquidity_sweep_reversal       <- NEW canonical implementation directly composing
                                 backend.market_structure's liquidity sweep + displacement +
                                 CHoCH/MSS break objects into the sweep->displacement->
                                 structure-shift sequence the audit found nowhere implemented
                                 (deprecates intelligence/trading::LiquiditySweepStrategy, a
                                 cruder HH/LL-only version; deprecates forex_strategies::
                                 liquidity_sweep_reversal_v1, metadata only)
  smc_continuation                <- NEW canonical implementation: HTF trend -> BOS ->
                                 displacement -> FVG/order-block retracement zone, per Part 7's
                                 worked example (deprecates intelligence/trading::
                                 MarketStructureStrategy, BOS/CHoCH-only; deprecates
                                 forex_frameworks::SMCFramework/ICTFramework, dashboard-only;
                                 deprecates strategies/registry.py::smc_continuation_v1, DSL-only)
  support_resistance_bounce        <- intelligence/trading/strategies.py::
                                 SupportResistanceBounceStrategy, adapted to read
                                 backend.market_structure LiquidityLevel objects instead of that
                                 pipeline's own naive rolling-window S/R
  momentum                          <- intelligence/trading/strategies.py::MomentumStrategy,
                                 using backend.core.technicals.rsi/macd (pure, IBKR-free)
  session_breakout                   <- NEW canonical implementation using
                                 backend.market_structure.sessions-derived session/previous-day
                                 levels (forex_strategies::session_breakout_v1 was metadata only)
  vwap_reversion                      <- re-derives the REAL cumulative-VWAP formula from
                                 backend/core/strategy_runner.py::_generate_vwap_reversion_signals
                                 (NOT intelligence/trading::VWAPStrategy, which the audit found
                                 is mislabeled -- it actually uses EMA20, not a VWAP formula)
  wyckoff                              <- NEW canonical implementation (2026-08-17), an
                                 independent strategy family, not a label added to an existing
                                 one -- composes backend.market_structure.wyckoff's point-in-time-
                                 safe accumulation/distribution schematic engine (itself built on
                                 the SAME swings/breaks/liquidity-sweep primitives every other
                                 family here already uses, see that module's own docstring for the
                                 volume-representation and lookback-window caveats it documents)
                                 into two explicit setups: a Spring/UTAD -> successful test ->
                                 SOS/SOW -> LPS/LPSY pullback entry, and a separate Phase-D/E
                                 continuation entry -- see evaluate_wyckoff's own docstring.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import pandas as pd

from backend.adaptive_management.tp_protection import construct_dynamic_stop
from backend.core.technicals import ema as _ema_series
from backend.core.technicals import macd as _macd_frame
from backend.core.technicals import rsi as _rsi_series
from backend.mt5_strategies.context import StrategyContext
from backend.mt5_strategies.models import StrategySignal, invalid_signal
from backend.market_structure.models import StructureBreakKind
from backend.market_structure.wyckoff import analyze_wyckoff

_MIN_REWARD_MULTIPLE = Decimal("1.5")


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
    return StrategySignal(
        strategy_id=strategy_id, strategy_family=family, symbol=ctx.symbol, broker_symbol=ctx.broker_symbol,
        direction=direction, timeframe=timeframe, generated_at=ctx.generated_at, valid=valid,
        raw_signal_strength=strength, proposed_entry=float(entry), stop_loss=float(stop), take_profit=float(target),
        reward_risk=float(rr) if rr is not None else None, regime=ctx.regime, evidence=evidence,
        rejection_reason=reason,
        metadata=metadata or {},
    )


def _no_signal(ctx: StrategyContext, *, strategy_id: str, family: str, timeframe: str, reason: str) -> StrategySignal:
    return invalid_signal(strategy_id, family, symbol=ctx.symbol, broker_symbol=ctx.broker_symbol, timeframe=timeframe, generated_at=ctx.generated_at, regime=ctx.regime, reason=reason)


def _dynamic_stop(
    ctx: StrategyContext, direction: str, entry: Decimal, structure_level: Decimal | float | None, atr: Decimal,
    *, min_atr_mult: float = 1.0, max_atr_mult: float = 3.0,
) -> tuple[Decimal | None, str]:
    """THE one canonical stop-construction path for every strategy in this module (Part 3 --
    "avoid 11 strategies independently calculating fragile stop geometry"). Bounds a proposed
    stop -- either a raw structural reference (a broken BOS level, a swept liquidity price, a
    session high/low) or a pure ATR multiple when no structural level applies -- between
    min_atr_mult x ATR and max_atr_mult x ATR, with a spread-aware buffer AND the broker's own
    minimum stop distance (ctx.broker_min_stop_distance, when known). Reuses the exact same
    utility MTFAI1's own stop construction already relies on (backend.adaptive_management.
    tp_protection.construct_dynamic_stop) rather than inventing a second stop engine.

    For the 8 strategies with no structural level, calling this with min_atr_mult==max_atr_mult
    equal to that strategy's existing single ATR multiple reproduces an IDENTICAL numeric stop
    to before this function existed for them -- the only behavior change is the new spread/
    broker-floor safety net, which can now reject (never silently widen past the strategy's own
    intended distance, and never silently accept a too-tight distance either).

    Why this exists: a barely-broken structural level can sit only a fraction of a pip from
    entry. The only validation these strategies had was a reward:risk RATIO floor
    (_bounded_rr/_MIN_REWARD_MULTIPLE), which a tiny stop with a proportionally tiny target
    happily clears while being tighter than typical spread -- all but guaranteeing a stop-out
    from normal noise rather than genuine adverse movement (confirmed live: a real NZDUSD
    breakout trade with a 1.3-pip stop). Returns (None, reason) -- never the raw, unbounded
    level -- when no safe distance can be constructed; the reason is one of the explicit
    rejection codes (Part 10), diagnosed by mirroring construct_dynamic_stop's own checks."""
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
    so would contradict the validation's own negative finding."""
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


# --------------------------------------------------------------------------- ema_trend ---
def evaluate_ema_trend(ctx: StrategyContext) -> StrategySignal:
    """EMA20/50/100 stack alignment + positive/negative slope on M15 (the intelligence/trading
    EMATrendStrategy pattern, EMA200 relaxed to EMA100 -- the standard 100-bar M15 fetch used
    elsewhere in this pipeline doesn't reliably support a 200-period EMA)."""
    closes = _closes(ctx.m15_rows)
    if len(closes) < 100:
        return _no_signal(ctx, strategy_id="ema_trend", family="ema_trend", timeframe="M15", reason="insufficient_history")
    ema20, ema50, ema100 = _ema_series(closes, 20), _ema_series(closes, 50), _ema_series(closes, 100)
    price = float(closes.iloc[-1])
    slope20 = float(ema20.iloc[-1] - ema20.iloc[-6])
    long_aligned = price > ema20.iloc[-1] > ema50.iloc[-1] > ema100.iloc[-1] and slope20 > 0
    short_aligned = price < ema20.iloc[-1] < ema50.iloc[-1] < ema100.iloc[-1] and slope20 < 0
    if not (long_aligned or short_aligned):
        return _no_signal(ctx, strategy_id="ema_trend", family="ema_trend", timeframe="M15", reason="ema_stack_not_aligned")
    direction = "LONG" if long_aligned else "SHORT"
    entry = Decimal(str(price))
    atr = ctx.atr_m15 or Decimal(str(abs(price - float(ema50.iloc[-1])) or 0.0001))
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, None, atr, min_atr_mult=1.5, max_atr_mult=1.5)
    if stop is None:
        return _no_signal(ctx, strategy_id="ema_trend", family="ema_trend", timeframe="M15", reason=stop_reason)
    target = entry + atr * Decimal("3.0") if direction == "LONG" else entry - atr * Decimal("3.0")
    strength = min(100.0, 60.0 + abs(slope20) / max(float(atr), 1e-9) * 20.0)
    # EQH/EQL + squeeze: evidence-only for this strategy (not yet OOS-validated here specifically
    # -- see _eqh_eql_touch_count/_squeeze_evidence docstrings). Never adjusts strength.
    eqh_eql_side = "sell_side" if direction == "LONG" else "buy_side"
    evidence = {
        "ema20": float(ema20.iloc[-1]), "ema50": float(ema50.iloc[-1]), "ema100": float(ema100.iloc[-1]), "slope20": slope20,
        "eqh_eql_touch_count": _eqh_eql_touch_count(ctx, side=eqh_eql_side, price=price, atr=float(atr)),
    }
    evidence.update(_squeeze_evidence(ctx))
    return _signal(ctx, strategy_id="ema_trend", family="ema_trend", timeframe="M15", direction=direction, strength=strength,
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=_geometry_metadata(ctx, entry, stop, None, atr, 1.5, 1.5))


# ----------------------------------------------------------------------- trend_pullback ---
def evaluate_trend_pullback(ctx: StrategyContext) -> StrategySignal:
    """M15 EMA20/50 local trend with price pulled back into the EMA20/50 zone (ATR-scaled
    tolerance), gated by H1 HTF trend non-conflict."""
    closes = _closes(ctx.m15_rows)
    if len(closes) < 60:
        return _no_signal(ctx, strategy_id="trend_pullback", family="trend_pullback", timeframe="M15", reason="insufficient_history")
    ema20, ema50 = _ema_series(closes, 20), _ema_series(closes, 50)
    price = float(closes.iloc[-1])
    atr = float(ctx.atr_m15) if ctx.atr_m15 else abs(price - float(ema50.iloc[-1])) or 0.0001
    tolerance = atr * 1.0
    zone_low, zone_high = min(ema20.iloc[-1], ema50.iloc[-1]) - tolerance, max(ema20.iloc[-1], ema50.iloc[-1]) + tolerance
    in_zone = zone_low <= price <= zone_high
    local_up = ema20.iloc[-1] > ema50.iloc[-1]
    htf_conflict = (local_up and ctx.htf_trend_h1 == "bearish") or (not local_up and ctx.htf_trend_h1 == "bullish")
    if not in_zone or htf_conflict:
        return _no_signal(ctx, strategy_id="trend_pullback", family="trend_pullback", timeframe="M15", reason="not_in_pullback_zone" if not in_zone else "htf_conflict")
    direction = "LONG" if local_up else "SHORT"
    entry = Decimal(str(price))
    atr_d = Decimal(str(atr))
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, None, atr_d, min_atr_mult=1.2, max_atr_mult=1.2)
    if stop is None:
        return _no_signal(ctx, strategy_id="trend_pullback", family="trend_pullback", timeframe="M15", reason=stop_reason)
    target = entry + atr_d * Decimal("2.5") if direction == "LONG" else entry - atr_d * Decimal("2.5")
    eqh_eql_side = "sell_side" if direction == "LONG" else "buy_side"
    evidence = {
        "ema20": float(ema20.iloc[-1]), "ema50": float(ema50.iloc[-1]), "htf_trend_h1": ctx.htf_trend_h1,
        "eqh_eql_touch_count": _eqh_eql_touch_count(ctx, side=eqh_eql_side, price=price, atr=atr),
    }
    evidence.update(_squeeze_evidence(ctx))
    return _signal(ctx, strategy_id="trend_pullback", family="trend_pullback", timeframe="M15", direction=direction, strength=70.0,
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=_geometry_metadata(ctx, entry, stop, None, atr_d, 1.2, 1.2))


# ------------------------------------------------------------------------------ breakout ---
def evaluate_breakout(ctx: StrategyContext) -> StrategySignal:
    """Reads the M15 BOS (continuation break) directly from the SMC engine rather than a
    separate support/resistance calculation. Requires a displacement-confirmed, recent break."""
    bos_breaks = [b for b in ctx.m15_snapshot.breaks if b.break_kind == StructureBreakKind.BOS.value]
    if not bos_breaks:
        return _no_signal(ctx, strategy_id="breakout", family="breakout", timeframe="M15", reason="no_bos")
    latest = max(bos_breaks, key=lambda b: b.bar_index)
    bars_since = (len(ctx.m15_rows) - 1) - latest.bar_index
    if bars_since > 5:
        return _no_signal(ctx, strategy_id="breakout", family="breakout", timeframe="M15", reason="bos_too_old")
    displaced = any(d.bar_index >= latest.bar_index - 1 and d.bar_index <= latest.bar_index + 1 for d in ctx.m15_snapshot.displacements)
    direction = "LONG" if latest.direction == "bullish" else "SHORT" if latest.direction == "bearish" else None
    if direction is None:
        return _no_signal(ctx, strategy_id="breakout", family="breakout", timeframe="M15", reason="ambiguous_break_direction")
    entry = Decimal(str(ctx.ask if direction == "LONG" else ctx.bid))
    atr = ctx.atr_m15 or Decimal(str(latest.break_distance or 0.0001))
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, latest.broken_level, atr)
    if stop is None:
        return _no_signal(ctx, strategy_id="breakout", family="breakout", timeframe="M15", reason=stop_reason)
    target = entry + atr * Decimal("2.5") if direction == "LONG" else entry - atr * Decimal("2.5")
    strength = 65.0 + (15.0 if displaced else 0.0) + (10.0 if bars_since <= 2 else 0.0)
    # EQH/EQL and squeeze evidence recorded for observability ONLY -- breakout's own OOS
    # validation for both was sign-unstable (EQH/EQL) or negative (squeeze); never adjusts
    # strength here, unlike support_resistance_bounce/smc_continuation's validated bonus.
    eqh_eql_side = "sell_side" if direction == "LONG" else "buy_side"
    eqh_eql_touches = _eqh_eql_touch_count(ctx, side=eqh_eql_side, price=float(entry), atr=float(atr))
    evidence = {"break_id": latest.id, "bars_since_bos": bars_since, "displacement_confirmed": displaced, "break_distance_atr": latest.break_distance_atr, "eqh_eql_touch_count": eqh_eql_touches}
    evidence.update(_squeeze_evidence(ctx))
    return _signal(ctx, strategy_id="breakout", family="breakout", timeframe="M15", direction=direction, strength=min(100.0, strength),
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=_geometry_metadata(ctx, entry, stop, latest.broken_level, atr, 1.0, 3.0))


# ------------------------------------------------------------------------ mean_reversion ---
def evaluate_mean_reversion(ctx: StrategyContext) -> StrategySignal:
    """M15 RSI(14) extremes. Regime suitability is enforced by the orchestrator's
    regime_compatible() gate (Part 5), not duplicated here."""
    closes = _closes(ctx.m15_rows)
    if len(closes) < 30:
        return _no_signal(ctx, strategy_id="mean_reversion", family="mean_reversion", timeframe="M15", reason="insufficient_history")
    rsi_series = _rsi_series(closes, 14)
    latest_rsi = float(rsi_series.iloc[-1]) if pd.notna(rsi_series.iloc[-1]) else None
    if latest_rsi is None or (30 < latest_rsi < 70):
        return _no_signal(ctx, strategy_id="mean_reversion", family="mean_reversion", timeframe="M15", reason="rsi_not_extreme")
    direction = "LONG" if latest_rsi <= 30 else "SHORT"
    price = float(closes.iloc[-1])
    entry = Decimal(str(price))
    atr = ctx.atr_m15 or Decimal("0.0001")
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, None, atr, min_atr_mult=1.2, max_atr_mult=1.2)
    if stop is None:
        return _no_signal(ctx, strategy_id="mean_reversion", family="mean_reversion", timeframe="M15", reason=stop_reason)
    target = entry + atr * Decimal("1.8") if direction == "LONG" else entry - atr * Decimal("1.8")
    strength = 55.0 + min(25.0, abs(50.0 - latest_rsi) - 20.0)
    eqh_eql_side = "sell_side" if direction == "LONG" else "buy_side"
    evidence = {"rsi14": latest_rsi, "eqh_eql_touch_count": _eqh_eql_touch_count(ctx, side=eqh_eql_side, price=price, atr=float(atr))}
    evidence.update(_squeeze_evidence(ctx))
    return _signal(ctx, strategy_id="mean_reversion", family="mean_reversion", timeframe="M15", direction=direction, strength=max(50.0, strength),
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=_geometry_metadata(ctx, entry, stop, None, atr, 1.2, 1.2))


# ----------------------------------------------------------------- liquidity_sweep_reversal ---
def evaluate_liquidity_sweep_reversal(ctx: StrategyContext) -> StrategySignal:
    """Genuine sweep -> displacement -> CHoCH/MSS sequencing (Part 7's worked example),
    composed entirely from already-detected backend.market_structure objects -- the sequencing
    itself, checked here, is the one piece the audit found nowhere in the engine."""
    sweeps = ctx.m15_snapshot.liquidity_sweeps
    if not sweeps:
        return _no_signal(ctx, strategy_id="liquidity_sweep_reversal", family="liquidity_sweep_reversal", timeframe="M15", reason="no_liquidity_sweep")
    latest_sweep = max(sweeps, key=lambda s: s.bar_index)
    reversal_direction = latest_sweep.direction  # "bullish" (swept sell-side) or "bearish" (swept buy-side)
    subsequent_displacement = [d for d in ctx.m15_snapshot.displacements if d.bar_index >= latest_sweep.bar_index and d.direction == reversal_direction]
    if not subsequent_displacement:
        return _no_signal(ctx, strategy_id="liquidity_sweep_reversal", family="liquidity_sweep_reversal", timeframe="M15", reason="no_displacement_after_sweep")
    displacement = min(subsequent_displacement, key=lambda d: d.bar_index)
    structure_shift = [
        b for b in ctx.m15_snapshot.breaks
        if b.bar_index >= displacement.bar_index and b.direction == reversal_direction and b.break_kind in {StructureBreakKind.CHOCH.value, StructureBreakKind.MSS.value}
    ]
    if not structure_shift:
        return _no_signal(ctx, strategy_id="liquidity_sweep_reversal", family="liquidity_sweep_reversal", timeframe="M15", reason="no_structure_shift_after_displacement")
    shift = min(structure_shift, key=lambda b: b.bar_index)
    bars_since_shift = (len(ctx.m15_rows) - 1) - shift.bar_index
    if bars_since_shift > 5:
        return _no_signal(ctx, strategy_id="liquidity_sweep_reversal", family="liquidity_sweep_reversal", timeframe="M15", reason="structure_shift_too_old")
    direction = "LONG" if reversal_direction == "bullish" else "SHORT"
    entry = Decimal(str(ctx.ask if direction == "LONG" else ctx.bid))
    atr = ctx.atr_m15 or Decimal("0.0001")
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, latest_sweep.swept_price, atr)
    if stop is None:
        return _no_signal(ctx, strategy_id="liquidity_sweep_reversal", family="liquidity_sweep_reversal", timeframe="M15", reason=stop_reason)
    target = entry + atr * Decimal("2.5") if direction == "LONG" else entry - atr * Decimal("2.5")
    is_mss = shift.break_kind == StructureBreakKind.MSS.value
    strength = 68.0 + (17.0 if is_mss else 5.0)
    # EQH/EQL evidence-only (n=15 on the real EURUSD OOS validation -- far too small to trust a
    # strength adjustment): was the level this strategy's own sweep touched ALSO a repeated-touch
    # EQH/EQL pool (cross-referencing the separate equal_level_sweeps list by bar/side), not just
    # a single-touch swing.
    swept_level_is_eqh_eql = any(s.bar_index == latest_sweep.bar_index and s.side == latest_sweep.side for s in ctx.m15_snapshot.equal_level_sweeps)
    evidence = {
        "sweep_id": latest_sweep.id, "displacement_id": displacement.id, "structure_shift_id": shift.id,
        "structure_shift_kind": shift.break_kind, "bars_since_shift": bars_since_shift,
        "swept_level_is_eqh_eql": swept_level_is_eqh_eql,
    }
    evidence.update(_squeeze_evidence(ctx))
    return _signal(ctx, strategy_id="liquidity_sweep_reversal", family="liquidity_sweep_reversal", timeframe="M15", direction=direction, strength=min(100.0, strength),
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=_geometry_metadata(ctx, entry, stop, latest_sweep.swept_price, atr, 1.0, 3.0))


# ---------------------------------------------------------------------------- smc_continuation ---
def evaluate_smc_continuation(ctx: StrategyContext) -> StrategySignal:
    """HTF trend (H4) -> M15 BOS (with-trend) -> displacement -> FVG/order-block retracement
    zone -> continuation entry, per Part 7's worked example."""
    if ctx.htf_trend_h4 not in {"bullish", "bearish"}:
        return _no_signal(ctx, strategy_id="smc_continuation", family="smc_continuation", timeframe="H4->M15", reason="no_htf_trend")
    with_trend_direction = "bullish" if ctx.htf_trend_h4 == "bullish" else "bearish"
    bos_breaks = [b for b in ctx.m15_snapshot.breaks if b.break_kind == StructureBreakKind.BOS.value and b.direction == with_trend_direction]
    if not bos_breaks:
        return _no_signal(ctx, strategy_id="smc_continuation", family="smc_continuation", timeframe="H4->M15", reason="no_with_trend_bos")
    latest_break = max(bos_breaks, key=lambda b: b.bar_index)
    displaced = any(d.bar_index >= latest_break.bar_index - 1 and d.direction == with_trend_direction for d in ctx.m15_snapshot.displacements)
    if not displaced:
        return _no_signal(ctx, strategy_id="smc_continuation", family="smc_continuation", timeframe="H4->M15", reason="no_displacement_confirmation")
    price = float(_closes(ctx.m15_rows).iloc[-1])
    retracement_zones = [z for z in ctx.m15_snapshot.imbalances if z.direction == with_trend_direction and z.status != "mitigated"]
    retracement_zones += [b for b in ctx.m15_snapshot.order_blocks if b.direction == with_trend_direction and b.status not in {"mitigated", "invalidated"}]
    in_retracement_zone = any(float(z.price_low) <= price <= float(z.price_high) for z in retracement_zones if z.price_low is not None and z.price_high is not None)
    direction = "LONG" if with_trend_direction == "bullish" else "SHORT"
    entry = Decimal(str(price))
    atr = ctx.atr_m15 or Decimal("0.0001")
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, None, atr, min_atr_mult=1.5, max_atr_mult=1.5)
    if stop is None:
        return _no_signal(ctx, strategy_id="smc_continuation", family="smc_continuation", timeframe="H4->M15", reason=stop_reason)
    target = entry + atr * Decimal("3.0") if direction == "LONG" else entry - atr * Decimal("3.0")
    eqh_eql_side = "sell_side" if direction == "LONG" else "buy_side"
    eqh_eql_touches = _eqh_eql_touch_count(ctx, side=eqh_eql_side, price=price, atr=float(atr))
    strength = 70.0 + (20.0 if in_retracement_zone else 0.0) + (10.0 if eqh_eql_touches >= 2 else 0.0)
    evidence = {
        "htf_trend_h4": ctx.htf_trend_h4, "bos_id": latest_break.id, "displacement_confirmed": displaced,
        "in_fvg_or_ob_retracement_zone": in_retracement_zone, "retracement_zone_count": len(retracement_zones),
        "eqh_eql_touch_count": eqh_eql_touches,
    }
    evidence.update(_squeeze_evidence(ctx))
    return _signal(ctx, strategy_id="smc_continuation", family="smc_continuation", timeframe="H4->M15", direction=direction, strength=min(100.0, strength),
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=_geometry_metadata(ctx, entry, stop, None, atr, 1.5, 1.5))


# --------------------------------------------------------------- support_resistance_bounce ---
def evaluate_support_resistance_bounce(ctx: StrategyContext) -> StrategySignal:
    """Price within ATR-scaled tolerance of a backend.market_structure LiquidityLevel (swing-
    derived, not a separate rolling-window S/R calc), with a rejecting last candle."""
    levels = ctx.m15_snapshot.liquidity_levels
    if not levels:
        return _no_signal(ctx, strategy_id="support_resistance_bounce", family="support_resistance_bounce", timeframe="M15", reason="no_liquidity_levels")
    price = float(_closes(ctx.m15_rows).iloc[-1])
    nearest = min(levels, key=lambda lv: abs(float(lv.level) - price))
    atr = float(ctx.atr_m15) if ctx.atr_m15 else 0.0001
    tolerance = max(float(nearest.tolerance), atr)
    if abs(float(nearest.level) - price) > tolerance:
        return _no_signal(ctx, strategy_id="support_resistance_bounce", family="support_resistance_bounce", timeframe="M15", reason="price_not_near_level")
    last = ctx.m15_rows[-1]
    bullish_reject = nearest.side == "sell_side" and float(last["close"]) > float(last["open"])
    bearish_reject = nearest.side == "buy_side" and float(last["close"]) < float(last["open"])
    if not (bullish_reject or bearish_reject):
        return _no_signal(ctx, strategy_id="support_resistance_bounce", family="support_resistance_bounce", timeframe="M15", reason="no_rejection_candle")
    direction = "LONG" if bullish_reject else "SHORT"
    entry = Decimal(str(price))
    atr_d = Decimal(str(atr))
    # Pure ATR stop, no structural level (entry is already essentially AT nearest.level by this
    # strategy's own trigger condition -- using it as the structural reference would collapse to
    # a near-zero raw distance every time, adding a spurious failure mode for no benefit since
    # min_atr_mult==max_atr_mult already fixes the distance regardless).
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, None, atr_d, min_atr_mult=1.2, max_atr_mult=1.2)
    if stop is None:
        return _no_signal(ctx, strategy_id="support_resistance_bounce", family="support_resistance_bounce", timeframe="M15", reason=stop_reason)
    target = entry + atr_d * Decimal("2.2") if direction == "LONG" else entry - atr_d * Decimal("2.2")
    eqh_eql_touches = _eqh_eql_touch_count(ctx, side=nearest.side, price=price, atr=atr)
    strength = 68.0 + (12.0 if eqh_eql_touches >= 2 else 0.0)
    evidence = {"level_id": nearest.id, "level": float(nearest.level), "side": nearest.side, "eqh_eql_touch_count": eqh_eql_touches}
    evidence.update(_squeeze_evidence(ctx))
    return _signal(ctx, strategy_id="support_resistance_bounce", family="support_resistance_bounce", timeframe="M15", direction=direction, strength=min(100.0, strength),
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=_geometry_metadata(ctx, entry, stop, None, atr_d, 1.2, 1.2))


# ------------------------------------------------------------------------------- momentum ---
def evaluate_momentum(ctx: StrategyContext) -> StrategySignal:
    """RSI + MACD alignment, no SMC dependency -- a pure momentum read."""
    closes = _closes(ctx.m15_rows)
    if len(closes) < 40:
        return _no_signal(ctx, strategy_id="momentum", family="momentum", timeframe="M15", reason="insufficient_history")
    rsi_series = _rsi_series(closes, 14)
    macd_frame = _macd_frame(closes)
    latest_rsi = float(rsi_series.iloc[-1]) if pd.notna(rsi_series.iloc[-1]) else 50.0
    macd_line, signal_line = float(macd_frame["macd"].iloc[-1]), float(macd_frame["signal"].iloc[-1])
    long_aligned = latest_rsi > 55 and macd_line > signal_line
    short_aligned = latest_rsi < 45 and macd_line < signal_line
    if not (long_aligned or short_aligned):
        return _no_signal(ctx, strategy_id="momentum", family="momentum", timeframe="M15", reason="momentum_not_aligned")
    direction = "LONG" if long_aligned else "SHORT"
    price = float(closes.iloc[-1])
    entry = Decimal(str(price))
    atr = ctx.atr_m15 or Decimal("0.0001")
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, None, atr, min_atr_mult=1.5, max_atr_mult=1.5)
    if stop is None:
        return _no_signal(ctx, strategy_id="momentum", family="momentum", timeframe="M15", reason=stop_reason)
    target = entry + atr * Decimal("2.5") if direction == "LONG" else entry - atr * Decimal("2.5")
    strength = 60.0 + min(30.0, abs(macd_line - signal_line) / max(abs(macd_line), 1e-9) * 30.0)
    eqh_eql_side = "sell_side" if direction == "LONG" else "buy_side"
    evidence = {
        "rsi14": latest_rsi, "macd": macd_line, "macd_signal": signal_line,
        "eqh_eql_touch_count": _eqh_eql_touch_count(ctx, side=eqh_eql_side, price=price, atr=float(atr)),
    }
    evidence.update(_squeeze_evidence(ctx))  # observability only -- see _squeeze_evidence's docstring
    return _signal(ctx, strategy_id="momentum", family="momentum", timeframe="M15", direction=direction, strength=min(100.0, strength),
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=_geometry_metadata(ctx, entry, stop, None, atr, 1.5, 1.5))


# ------------------------------------------------------------------------ session_breakout ---
def evaluate_session_breakout(ctx: StrategyContext) -> StrategySignal:
    """Breakout beyond the most recently completed session's high/low (Asian/London/NY,
    whichever the market_structure engine's configured session windows produced), or the
    previous day's high/low -- levels already computed by backend.market_structure.sessions,
    never re-derived here."""
    levels = ctx.m15_snapshot.session_levels
    if not levels:
        return _no_signal(ctx, strategy_id="session_breakout", family="session_breakout", timeframe="M15", reason="no_session_levels")
    price = float(_closes(ctx.m15_rows).iloc[-1])
    highs = [lv for lv in levels if lv.level_name in {"high", "previous_day_high"}]
    lows = [lv for lv in levels if lv.level_name in {"low", "previous_day_low"}]
    broken_high = max((lv for lv in highs if price > float(lv.level)), key=lambda lv: lv.level, default=None)
    broken_low = min((lv for lv in lows if price < float(lv.level)), key=lambda lv: lv.level, default=None)
    if broken_high is None and broken_low is None:
        return _no_signal(ctx, strategy_id="session_breakout", family="session_breakout", timeframe="M15", reason="no_session_level_broken")
    direction = "LONG" if broken_high is not None else "SHORT"
    reference = broken_high if broken_high is not None else broken_low
    entry = Decimal(str(price))
    atr = ctx.atr_m15 or Decimal("0.0001")
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, reference.level, atr)
    if stop is None:
        return _no_signal(ctx, strategy_id="session_breakout", family="session_breakout", timeframe="M15", reason=stop_reason)
    target = entry + atr * Decimal("2.5") if direction == "LONG" else entry - atr * Decimal("2.5")
    eqh_eql_side = "sell_side" if direction == "LONG" else "buy_side"
    evidence = {
        "session": reference.session_name, "level_name": reference.level_name, "level": float(reference.level),
        "eqh_eql_touch_count": _eqh_eql_touch_count(ctx, side=eqh_eql_side, price=price, atr=float(atr)),
    }
    evidence.update(_squeeze_evidence(ctx))  # observability only -- see _squeeze_evidence's docstring
    return _signal(ctx, strategy_id="session_breakout", family="session_breakout", timeframe="M15", direction=direction, strength=64.0,
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=_geometry_metadata(ctx, entry, stop, reference.level, atr, 1.0, 3.0))


# -------------------------------------------------------------------------- vwap_reversion ---
def evaluate_vwap_reversion(ctx: StrategyContext) -> StrategySignal:
    """Session-anchored cumulative VWAP (close*volume cumsum / volume cumsum -- the same real
    formula as backend/core/strategy_runner.py's _generate_vwap_reversion_signals, re-derived
    here for a single live evaluation rather than importing that backtest-array-oriented
    function) with a volume-confirmed deviation-based reversion signal. NOT the mislabeled
    intelligence/trading::VWAPStrategy, which the audit found actually uses EMA20."""
    if len(ctx.m15_rows) < 30:
        return _no_signal(ctx, strategy_id="vwap_reversion", family="vwap_reversion", timeframe="M15", reason="insufficient_history")
    closes = pd.Series([float(r["close"]) for r in ctx.m15_rows])
    volumes = pd.Series([float(r.get("tick_volume") or 0) for r in ctx.m15_rows]).replace(0, pd.NA)
    if volumes.isna().all():
        return _no_signal(ctx, strategy_id="vwap_reversion", family="vwap_reversion", timeframe="M15", reason="no_volume_data")
    cumulative_vwap = (closes * volumes).cumsum() / volumes.cumsum()
    vwap_now = float(cumulative_vwap.iloc[-1])
    price = float(closes.iloc[-1])
    avg_volume = volumes.fillna(0).rolling(20, min_periods=1).mean().iloc[-1]
    volume_now = float(volumes.fillna(0).iloc[-1])
    deviation_pct = (price - vwap_now) / vwap_now if vwap_now else 0.0
    volume_confirmed = volume_now > float(avg_volume) * 1.3 if avg_volume else False
    if deviation_pct < -0.0015 and volume_confirmed:
        direction = "LONG"
    elif deviation_pct > 0.0015:
        direction = "SHORT"
    else:
        return _no_signal(ctx, strategy_id="vwap_reversion", family="vwap_reversion", timeframe="M15", reason="price_not_deviated_from_vwap")
    entry = Decimal(str(price))
    atr = ctx.atr_m15 or Decimal("0.0001")
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, None, atr, min_atr_mult=1.2, max_atr_mult=1.2)
    if stop is None:
        return _no_signal(ctx, strategy_id="vwap_reversion", family="vwap_reversion", timeframe="M15", reason=stop_reason)
    target = Decimal(str(vwap_now))
    eqh_eql_side = "sell_side" if direction == "LONG" else "buy_side"
    evidence = {
        "vwap": vwap_now, "deviation_pct": deviation_pct, "volume_confirmed": volume_confirmed,
        "eqh_eql_touch_count": _eqh_eql_touch_count(ctx, side=eqh_eql_side, price=price, atr=float(atr)),
    }
    evidence.update(_squeeze_evidence(ctx))
    return _signal(ctx, strategy_id="vwap_reversion", family="vwap_reversion", timeframe="M15", direction=direction, strength=58.0,
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=_geometry_metadata(ctx, entry, stop, None, atr, 1.2, 1.2))


# --------------------------------------------------------------------------------- wyckoff ---
def evaluate_wyckoff(ctx: StrategyContext) -> StrategySignal:
    """Wyckoff accumulation/distribution schematic strategy -- an INDEPENDENT strategy family
    (not a label added to an existing one). All schematic/phase/event detection lives in
    backend.market_structure.wyckoff::analyze_wyckoff (point-in-time-safe, itself built only on
    the same swings/structure-breaks/liquidity-sweeps every other family here already reuses --
    see that module's docstring for the volume-representation and ~100-bar-window caveats). This
    function only decides, given that analysis, whether a TRADEABLE setup exists right now.

    Two explicit, separately-tagged setups (evidence["setup"]):
      spring_sos_lps       -- Spring (accumulation) or Upthrust/UTAD (distribution) has occurred
                               AND been successfully tested (phase reached C or later -- a failed
                               test means analysis.invalidated and this function already returned
                               NO_TRADE above), AND Sign-of-Strength/Weakness has confirmed (phase
                               D/E). Entered at the LPS/LPSY pullback price when that swing has
                               already confirmed, or, if it hasn't confirmed yet, at a live
                               pullback currently holding the just-broken SOS/SOW level within one
                               ATR -- avoids waiting for a swing's own right-bar confirmation delay
                               to chase an entry that has already run away.
      phase_d_continuation -- SOS/SOW has confirmed (phase D or E) but price is NOT at a specific
                               pullback right now -- a plain continuation entry. Deliberately
                               tagged separately from spring_sos_lps so historical validation can
                               tell whether this weaker-evidence entry independently holds up
                               rather than assuming it shares the other setup's edge.

    A schematic with no Automatic Rally yet, no climax at all, an invalidated Spring/UTAD (broke
    past the tested extreme), or no SOS/SOW confirmation yet (phase A/B/C_untested/C) produces
    NO_TRADE -- this strategy only trades a schematic that has already earned BOTH a successful
    extreme test AND a strength/weakness confirmation, never an in-progress range on spec."""
    analysis = analyze_wyckoff(ctx.m15_rows, ctx.m15_snapshot, symbol=ctx.broker_symbol, timeframe="M15")
    if analysis.schematic == "none":
        return _no_signal(ctx, strategy_id="wyckoff", family="wyckoff", timeframe="M15", reason="no_schematic_detected")
    if analysis.invalidated:
        return _no_signal(ctx, strategy_id="wyckoff", family="wyckoff", timeframe="M15", reason=analysis.invalidation_reason or "spring_or_utad_invalidated")
    if analysis.phase not in {"D", "E"}:
        return _no_signal(ctx, strategy_id="wyckoff", family="wyckoff", timeframe="M15", reason=f"phase_{analysis.phase}_not_yet_tradeable")

    direction = "LONG" if analysis.schematic == "accumulation" else "SHORT"
    sos_event = analysis.event("SOS") if direction == "LONG" else analysis.event("SOW")
    if sos_event is None:
        return _no_signal(ctx, strategy_id="wyckoff", family="wyckoff", timeframe="M15", reason="no_sos_sow_confirmation")

    price = float(_closes(ctx.m15_rows).iloc[-1])
    atr = ctx.atr_m15 or Decimal("0.0001")
    atr_f = float(atr)
    tolerance = atr_f * 1.0

    lps_event = analysis.event("LPS") if direction == "LONG" else analysis.event("LPSY")
    spring_event = analysis.event("SPRING") if direction == "LONG" else analysis.event("UTAD")
    broken_level = sos_event.evidence.get("broken_level")

    at_lps = lps_event is not None and abs(price - lps_event.price) <= tolerance
    holding_broken_level = broken_level is not None and (
        (direction == "LONG" and price >= broken_level and (price - broken_level) <= tolerance)
        or (direction == "SHORT" and price <= broken_level and (broken_level - price) <= tolerance)
    )

    if at_lps or holding_broken_level:
        setup = "spring_sos_lps"
        structure_ref = lps_event.price if lps_event is not None else (broken_level if broken_level is not None else (spring_event.price if spring_event is not None else None))
        min_mult, max_mult = 1.0, 3.0
    else:
        setup = "phase_d_continuation"
        structure_ref = broken_level if broken_level is not None else (spring_event.price if spring_event is not None else None)
        min_mult, max_mult = 1.5, 3.0

    entry = Decimal(str(price))
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, structure_ref, atr, min_atr_mult=min_mult, max_atr_mult=max_mult)
    if stop is None:
        return _no_signal(ctx, strategy_id="wyckoff", family="wyckoff", timeframe="M15", reason=stop_reason)

    # Wyckoff-native target: project the trading range's own width ("cause") from the breakout as
    # the expected move ("effect") -- a simplified point-and-figure-style count using data this
    # module already has, rather than a bare ATR multiple. Floored at 2x ATR so a narrow range
    # never produces a target inside ordinary noise.
    range_span = (analysis.range_high - analysis.range_low) if (analysis.range_high is not None and analysis.range_low is not None) else 0.0
    projected_move = Decimal(str(max(range_span, atr_f * 2.0)))
    target = entry + projected_move if direction == "LONG" else entry - projected_move

    eqh_eql_side = "sell_side" if direction == "LONG" else "buy_side"
    strength = 62.0 + (15.0 if setup == "spring_sos_lps" else 0.0) + (10.0 if lps_event is not None or analysis.phase == "D" else 0.0)
    evidence = {
        "setup": setup, "schematic": analysis.schematic, "phase": analysis.phase,
        "range_low": analysis.range_low, "range_high": analysis.range_high, "range_position": analysis.range_position,
        "events": [{"type": e.event_type, "bar_index": e.bar_index, "price": e.price, "evidence": e.evidence} for e in analysis.events],
        "eqh_eql_touch_count": _eqh_eql_touch_count(ctx, side=eqh_eql_side, price=price, atr=atr_f),
    }
    evidence.update(_squeeze_evidence(ctx))
    return _signal(ctx, strategy_id="wyckoff", family="wyckoff", timeframe="M15", direction=direction, strength=min(100.0, strength),
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=_geometry_metadata(ctx, entry, stop, structure_ref, atr, min_mult, max_mult))


EVALUATORS: dict[str, Any] = {
    "ema_trend": evaluate_ema_trend,
    "trend_pullback": evaluate_trend_pullback,
    "breakout": evaluate_breakout,
    "mean_reversion": evaluate_mean_reversion,
    "liquidity_sweep_reversal": evaluate_liquidity_sweep_reversal,
    "smc_continuation": evaluate_smc_continuation,
    "support_resistance_bounce": evaluate_support_resistance_bounce,
    "momentum": evaluate_momentum,
    "session_breakout": evaluate_session_breakout,
    "vwap_reversion": evaluate_vwap_reversion,
    "wyckoff": evaluate_wyckoff,
}


def evaluate_all(ctx: StrategyContext, *, strategy_ids: list[str] | None = None) -> list[StrategySignal]:
    """Evaluates every registered non-mtfai1 strategy family that is regime-compatible for the
    current context (Part 5) -- incompatible strategies are skipped entirely, not scored as
    invalid, so they never appear in the candidate pool for this cycle.

    Also enforces the demo-only operational circuit breaker (Part 17): a tripped strategy is
    skipped (never evaluated, never contributes a candidate) until an operator resets it, and
    every produced signal is sanity-checked (geometry/finite-price/RR) so a bug can never
    reach fusion/execution even if a strategy's own reward:risk logic missed it. This has
    nothing to do with trading performance -- losing trades never trip the breaker."""
    from backend.mt5_strategies.models import DISABLED, activation_status, regime_compatible
    from backend.mt5_strategies import circuit_breaker

    ids = strategy_ids if strategy_ids is not None else list(EVALUATORS.keys())
    signals: list[StrategySignal] = []
    for strategy_id in ids:
        evaluator = EVALUATORS.get(strategy_id)
        if evaluator is None or not regime_compatible(strategy_id, ctx.regime):
            continue
        if circuit_breaker.is_tripped(strategy_id):
            signals.append(_no_signal(ctx, strategy_id=strategy_id, family=strategy_id, timeframe="M15", reason="CIRCUIT_BREAKER_OPEN"))
            continue
        # Part 16: DISABLED means genuinely out of the pipeline -- unlike SHADOW_MT5 (still
        # evaluated for calibration, just never executable), a DISABLED strategy is not
        # evaluated at all.
        if activation_status(strategy_id) == DISABLED:
            signals.append(_no_signal(ctx, strategy_id=strategy_id, family=strategy_id, timeframe="M15", reason="STRATEGY_DISABLED"))
            continue
        try:
            signal = evaluator(ctx)
        except Exception:
            circuit_breaker.record_evaluation_error(strategy_id)
            signals.append(_no_signal(ctx, strategy_id=strategy_id, family=strategy_id, timeframe="M15", reason="evaluation_error"))
            continue
        circuit_breaker.record_evaluation_success(strategy_id)
        malformed_reason = circuit_breaker.validate_signal_sanity(signal)
        if malformed_reason is not None:
            circuit_breaker.record_malformed_signal(strategy_id)
            signal = _no_signal(ctx, strategy_id=strategy_id, family=signal.strategy_family, timeframe=signal.timeframe, reason=malformed_reason)
        signals.append(signal)
    return signals
