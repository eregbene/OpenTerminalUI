"""breakout -- negative in every one of 9 calendar years (2018-2026) across the full 8-year
authoritative audit (94,079 trades, -0.183R pooled, PF 0.76, 42.2% immediate-failure rate in the
March-2021 screening pass). Reads the M15 BOS (continuation break) directly from the SMC engine
rather than a separate support/resistance calculation.

2026-08-20 architecture blueprint (Phase 1), Section 3.3: four flagged levers targeting the
audit's own diagnosis (bad entry timing / stop geometry, not direction -- LONG and SHORT are
almost identically negative). 2026-08-21 (Phase 2) adds the engine-wide ADX regime gate (CHOP_
RANGING disables this strategy outright; HIGH_VOLATILITY_EXPANSION widens its ATR buffer; QUIET_
COMPRESSION primes retest-and-hold mode), the session-liquidity timing filter (this is one of the
3 restricted strategies), stale-exit metadata, and the spread safety buffer. 2026-08-21 (Phase 3)
adds an inducement (IDM) precondition on the triggering break -- reuses smc_continuation.py's
exact _liquidity_sweep_precedes helper, not a second implementation -- and structural take-profit
targeting. With every flag at its default, `evaluate_breakout` is behavior-identical to the
pre-restructure implementation -- same rejection reasons, same evidence keys, same strength
formula, same stop/target math. Nothing here changes risk-per-trade, stop/target geometry
formulas, or the minimum reward:risk floor (_shared._MIN_REWARD_MULTIPLE, untouched).
"""
from __future__ import annotations

import os
from decimal import Decimal

from backend.market_structure.models import StructureBreak, StructureBreakKind
from backend.mt5_strategies.context import StrategyContext
from backend.mt5_strategies.families._shared import (
    _consolidation_quality_score,
    _dynamic_stop,
    _env_flag,
    _env_float,
    _env_int,
    _eqh_eql_touch_count,
    _geometry_metadata,
    _liquidity_sweep_precedes,
    _no_signal,
    _regime_atr_mult_scale,
    _regime_prefers_breakout_retest,
    _regime_strategy_disabled,
    _session_timing_permits,
    _signal,
    _spread_within_safety_buffer,
    _squeeze_evidence,
    _stale_exit_metadata,
    _structural_take_profit,
)
from backend.mt5_strategies.models import StrategySignal

_STRATEGY_ID = "breakout"
_BREAK_AND_GO = "BREAK_AND_GO"
_RETEST_AND_HOLD = "RETEST_AND_HOLD"


def _atr_buffer_met(break_obj: StructureBreak) -> bool:
    """MT5_BREAKOUT_ATR_BUFFER_ENABLED (default False): require the break to have exceeded the
    level by at least MT5_BREAKOUT_ATR_BUFFER_MULT (default 0.5) ATRs, using break_distance_atr
    already computed by the SMC engine -- no new distance calculation. Research consensus
    starting point (0.5x ATR beyond a level before trusting a trend-continuation break)."""
    if not _env_flag("MT5_BREAKOUT_ATR_BUFFER_ENABLED", False):
        return True
    buffer_mult = _env_float("MT5_BREAKOUT_ATR_BUFFER_MULT", 0.5)
    return break_obj.break_distance_atr is not None and break_obj.break_distance_atr >= buffer_mult


def _htf_aligned(ctx: StrategyContext, direction: str) -> bool:
    """MT5_BREAKOUT_HTF_GATE_ENABLED (default False): only take a breakout whose direction
    agrees with the H1 trend -- the same ctx.htf_trend_h1 field already computed for every
    cycle, mirroring smc_continuation.py's existing H4 gate pattern."""
    if not _env_flag("MT5_BREAKOUT_HTF_GATE_ENABLED", False):
        return True
    expected = "bullish" if direction == "LONG" else "bearish"
    return ctx.htf_trend_h1 == expected


def _find_retest_hold(ctx: StrategyContext, break_obj: StructureBreak, direction: str) -> tuple[int, float] | None:
    """Scans the bars after `break_obj` for a completed retest-and-hold: a pullback that
    approached the broken level, never closed/wicked back through it, and has since moved away
    again. Returns (confirmation_bar_index, closest_approach_price), or None if no such pattern
    has completed yet in the current ~100-bar window -- this strategy is stateless like every
    other evaluator here, so "waiting for a retest" means "look for one already visible in the
    fetched history", not carrying state across cycles."""
    rows = ctx.m15_rows
    break_idx = break_obj.bar_index
    if break_idx + 2 >= len(rows):
        return None  # need at least one bar to pull back and one more to confirm the hold
    post_break = rows[break_idx + 1:]
    broken_level = float(break_obj.broken_level)
    if direction == "LONG":
        closest_rel_idx, closest_row = min(enumerate(post_break), key=lambda pair: float(pair[1]["low"]))
        closest_price = float(closest_row["low"])
        if closest_price <= broken_level:
            return None  # wicked/closed back through the level -- not a hold
        if closest_rel_idx >= len(post_break) - 1:
            return None  # no bar yet to confirm the bounce away
        if float(post_break[-1]["close"]) <= closest_price:
            return None  # hasn't actually moved away again
        return break_idx + 1 + closest_rel_idx, closest_price
    if direction == "SHORT":
        closest_rel_idx, closest_row = max(enumerate(post_break), key=lambda pair: float(pair[1]["high"]))
        closest_price = float(closest_row["high"])
        if closest_price >= broken_level:
            return None
        if closest_rel_idx >= len(post_break) - 1:
            return None
        if float(post_break[-1]["close"]) >= closest_price:
            return None
        return break_idx + 1 + closest_rel_idx, closest_price
    return None


def _direction_of(break_obj: StructureBreak) -> str | None:
    if break_obj.direction == "bullish":
        return "LONG"
    if break_obj.direction == "bearish":
        return "SHORT"
    return None


def _idm_confirmed(ctx: StrategyContext, break_obj: StructureBreak) -> bool:
    """MT5_BREAKOUT_IDM_REQUIRED (Phase 3 Section 3, default False): require a liquidity sweep,
    on the side that would trap counter-trend participants, before the triggering break's own
    bar -- the same "no sweep, no trade" inducement precondition smc_continuation.py already
    ships (_liquidity_sweep_precedes), reused here rather than reimplemented. Targets this
    strategy's own worst diagnosis: entering directly on a level break with no inducement context
    is exactly the stop-run-fakeout pattern behind its 45.1% immediate-failure rate."""
    if not _env_flag("MT5_BREAKOUT_IDM_REQUIRED", False):
        return True
    return _liquidity_sweep_precedes(ctx, before_bar_index=break_obj.bar_index, trend_direction=break_obj.direction)


def _build_signal(ctx: StrategyContext, *, direction: str, entry: Decimal, structure_ref: Decimal | float | None,
                   atr: Decimal, evidence: dict, strength: float) -> StrategySignal:
    max_atr_mult = 3.0 * _regime_atr_mult_scale(ctx)
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, structure_ref, atr, min_atr_mult=1.0, max_atr_mult=max_atr_mult)
    if stop is None:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason=stop_reason)
    # Phase 3 Section 1 (default off): structural target (nearest opposing unmitigated liquidity)
    # instead of a flat ATR multiple -- falls back to the original formula unchanged when the
    # flag is off or no structural level qualifies. See _shared.py's _structural_take_profit.
    structural = _structural_take_profit(ctx, direction=direction, entry=entry, stop=stop, atr=atr)
    if structural is not None:
        target = structural["tp1"]
        tp_basis = structural["basis"]
    else:
        target = entry + atr * Decimal("2.5") if direction == "LONG" else entry - atr * Decimal("2.5")
        tp_basis = "atr_flat_multiple"
    consolidation_score = _consolidation_quality_score(ctx, reference_level=structure_ref, atr=float(atr)) if structure_ref is not None else 0.0
    evidence = {**evidence, "consolidation_quality_score": consolidation_score, "market_regime": ctx.market_regime, "take_profit_basis": tp_basis}
    evidence.update(_squeeze_evidence(ctx))
    metadata = _geometry_metadata(ctx, entry, stop, structure_ref, atr, 1.0, max_atr_mult)
    metadata.update(_stale_exit_metadata(ctx, strategy_id=_STRATEGY_ID))
    return _signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", direction=direction, strength=min(100.0, strength),
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=metadata)


def _evaluate_break_and_go(ctx: StrategyContext, bos_breaks: list[StructureBreak]) -> StrategySignal:
    """Original break-and-go logic, unchanged, with the two new gates checked as additional
    preconditions (both default-off, so default behavior is byte-identical to before this
    restructure)."""
    latest = max(bos_breaks, key=lambda b: b.bar_index)
    bars_since = (len(ctx.m15_rows) - 1) - latest.bar_index
    if bars_since > 5:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="bos_too_old")
    direction = _direction_of(latest)
    if direction is None:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="ambiguous_break_direction")
    if not _atr_buffer_met(latest):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="ATR_BUFFER_NOT_MET")
    if not _htf_aligned(ctx, direction):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="HTF_TREND_CONFLICT")
    if not _idm_confirmed(ctx, latest):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="NO_INDUCEMENT_SWEEP_BEFORE_BREAK")
    displaced = any(d.bar_index >= latest.bar_index - 1 and d.bar_index <= latest.bar_index + 1 for d in ctx.m15_snapshot.displacements)
    entry = Decimal(str(ctx.ask if direction == "LONG" else ctx.bid))
    atr = ctx.atr_m15 or Decimal(str(latest.break_distance or 0.0001))
    strength = 65.0 + (15.0 if displaced else 0.0) + (10.0 if bars_since <= 2 else 0.0)
    # EQH/EQL and squeeze evidence recorded for observability ONLY -- breakout's own OOS
    # validation for both was sign-unstable (EQH/EQL) or negative (squeeze); never adjusts
    # strength here, unlike support_resistance_bounce/smc_continuation's validated bonus.
    eqh_eql_side = "sell_side" if direction == "LONG" else "buy_side"
    eqh_eql_touches = _eqh_eql_touch_count(ctx, side=eqh_eql_side, price=float(entry), atr=float(atr))
    evidence = {
        "entry_mode": _BREAK_AND_GO, "break_id": latest.id, "bars_since_bos": bars_since,
        "displacement_confirmed": displaced, "break_distance_atr": latest.break_distance_atr,
        "eqh_eql_touch_count": eqh_eql_touches,
    }
    return _build_signal(ctx, direction=direction, entry=entry, structure_ref=latest.broken_level, atr=atr, evidence=evidence, strength=strength)


def _evaluate_retest_and_hold(ctx: StrategyContext, bos_breaks: list[StructureBreak]) -> StrategySignal:
    """MT5_BREAKOUT_ENTRY_MODE=RETEST_AND_HOLD: a genuinely separate entry path (not a tweak to
    break-and-go) so both can be fingerprinted and OOS-compared directly. Widened lookback
    (MT5_BREAKOUT_RETEST_LOOKBACK_BARS, default 20 bars) since this mode is explicitly waiting
    for a SECOND event (the hold) after the break, not just the break itself."""
    lookback = _env_int("MT5_BREAKOUT_RETEST_LOOKBACK_BARS", 20)
    last_index = len(ctx.m15_rows) - 1
    candidates = sorted((b for b in bos_breaks if last_index - b.bar_index <= lookback), key=lambda b: b.bar_index, reverse=True)
    if not candidates:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="no_bos")
    for candidate in candidates:
        direction = _direction_of(candidate)
        if direction is None:
            continue
        if not _atr_buffer_met(candidate) or not _htf_aligned(ctx, direction):
            continue
        if not _idm_confirmed(ctx, candidate):
            continue
        hold = _find_retest_hold(ctx, candidate, direction)
        if hold is None:
            continue
        confirm_bar_index, closest_price = hold
        bars_since_confirm = last_index - confirm_bar_index
        if bars_since_confirm > 5:
            continue
        entry = Decimal(str(ctx.ask if direction == "LONG" else ctx.bid))
        atr = ctx.atr_m15 or Decimal(str(candidate.break_distance or 0.0001))
        strength = 65.0 + (15.0 if bars_since_confirm <= 2 else 5.0)
        evidence = {
            "entry_mode": _RETEST_AND_HOLD, "break_id": candidate.id, "break_distance_atr": candidate.break_distance_atr,
            "retest_confirm_bar_index": confirm_bar_index, "bars_since_retest_confirm": bars_since_confirm,
            "closest_retest_price": closest_price,
        }
        return _build_signal(ctx, direction=direction, entry=entry, structure_ref=candidate.broken_level, atr=atr, evidence=evidence, strength=strength)
    return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="no_confirmed_retest_and_hold")


def evaluate_breakout(ctx: StrategyContext) -> StrategySignal:
    """Requires a BOS; dispatches to break-and-go (default) or retest-and-hold per
    MT5_BREAKOUT_ENTRY_MODE (or, when that's left unset, per QUIET_COMPRESSION's retest-mode
    preference -- Phase 2 Section 1). See module docstring for the audit evidence this targets."""
    if _regime_strategy_disabled(ctx, _STRATEGY_ID):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="REGIME_DISABLED_CHOP_RANGING")
    if not _session_timing_permits(ctx, _STRATEGY_ID):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="OUTSIDE_PEAK_LIQUIDITY_WINDOW")
    if not _spread_within_safety_buffer(ctx):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="SPREAD_SAFETY_BUFFER_EXCEEDED")

    bos_breaks = [b for b in ctx.m15_snapshot.breaks if b.break_kind == StructureBreakKind.BOS.value]
    if not bos_breaks:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="no_bos")
    explicit_mode = os.environ.get("MT5_BREAKOUT_ENTRY_MODE")
    if explicit_mode is not None:
        mode = explicit_mode.strip().upper()
    elif _regime_prefers_breakout_retest(ctx):
        mode = _RETEST_AND_HOLD
    else:
        mode = _BREAK_AND_GO
    if mode == _RETEST_AND_HOLD:
        return _evaluate_retest_and_hold(ctx, bos_breaks)
    return _evaluate_break_and_go(ctx, bos_breaks)
