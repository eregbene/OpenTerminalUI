"""smc_continuation -- positive at every window size in the 8-year audit (+0.036R pooled,
45,874 trades, 6 of 10 symbols positive), with an accelerating edge in 2026 (+0.474R, 49.1% win
rate). The March-2021 screening pass overstated its magnitude by roughly 25x (+0.885R) by
catching an unusually strong month -- the edge is real but modest. HTF trend (H4) -> M15 BOS
(with-trend) -> displacement -> FVG/order-block retracement zone -> continuation entry.

2026-08-20 architecture blueprint (Phase 1), Section 3.6: one flagged lever (inducement
precondition on the triggering BOS) plus unconditional, observability-only displacement-magnitude
evidence. 2026-08-21 (Phase 2) adds regime-widened ATR buffers under HIGH_VOLATILITY_EXPANSION and
the spread safety buffer -- this strategy is NOT regime-disabled or session-timing-restricted per
the Phase 2 spec's tables. With every flag at its default (all False), `evaluate_smc_continuation`
is behavior-identical to the pre-restructure implementation. Does not modify liquidity_sweep_
reversal.py's own sweep-sequencing logic -- reuses its same ctx.m15_snapshot.liquidity_sweeps
data, never its code.
"""
from __future__ import annotations

from decimal import Decimal

from backend.market_structure.models import StructureBreakKind
from backend.mt5_strategies.context import StrategyContext
from backend.mt5_strategies.families._shared import (
    _closes,
    _dynamic_stop,
    _env_flag,
    _eqh_eql_touch_count,
    _geometry_metadata,
    _liquidity_sweep_precedes,
    _no_signal,
    _regime_atr_mult_scale,
    _signal,
    _spread_within_safety_buffer,
    _squeeze_evidence,
)
from backend.mt5_strategies.models import StrategySignal

_STRATEGY_ID = "smc_continuation"


def evaluate_smc_continuation(ctx: StrategyContext) -> StrategySignal:
    if not _spread_within_safety_buffer(ctx):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="H4->M15", reason="SPREAD_SAFETY_BUFFER_EXCEEDED")
    if ctx.htf_trend_h4 not in {"bullish", "bearish"}:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="H4->M15", reason="no_htf_trend")
    with_trend_direction = "bullish" if ctx.htf_trend_h4 == "bullish" else "bearish"
    bos_breaks = [b for b in ctx.m15_snapshot.breaks if b.break_kind == StructureBreakKind.BOS.value and b.direction == with_trend_direction]
    if not bos_breaks:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="H4->M15", reason="no_with_trend_bos")
    latest_break = max(bos_breaks, key=lambda b: b.bar_index)

    # Inducement (IDM) precondition (Section 3.6, default off): require a liquidity sweep, in
    # the side that would trap counter-trend participants, before this BOS -- research consensus
    # "no sweep, no trade" rule, targeting the audit's symbol concentration (5 of 10 symbols
    # negative) without touching the with-trend logic that already works on the other 5.
    if _env_flag("MT5_SMC_CONTINUATION_IDM_REQUIRED", False):
        if not _liquidity_sweep_precedes(ctx, before_bar_index=latest_break.bar_index, trend_direction=with_trend_direction):
            return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="H4->M15", reason="NO_INDUCEMENT_SWEEP_BEFORE_BOS")

    qualifying_displacements = [d for d in ctx.m15_snapshot.displacements if d.bar_index >= latest_break.bar_index - 1 and d.direction == with_trend_direction]
    if not qualifying_displacements:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="H4->M15", reason="no_displacement_confirmation")
    # Strongest qualifying displacement, for the new magnitude evidence below -- `any(...)`'s
    # original pass/fail semantics are unchanged; this only picks WHICH one to report on.
    strongest_displacement = max(qualifying_displacements, key=lambda d: d.magnitude_atr or 0.0)

    price = float(_closes(ctx.m15_rows).iloc[-1])
    retracement_zones = [z for z in ctx.m15_snapshot.imbalances if z.direction == with_trend_direction and z.status != "mitigated"]
    retracement_zones += [b for b in ctx.m15_snapshot.order_blocks if b.direction == with_trend_direction and b.status not in {"mitigated", "invalidated"}]
    in_retracement_zone = any(float(z.price_low) <= price <= float(z.price_high) for z in retracement_zones if z.price_low is not None and z.price_high is not None)
    direction = "LONG" if with_trend_direction == "bullish" else "SHORT"
    entry = Decimal(str(price))
    atr = ctx.atr_m15 or Decimal("0.0001")
    max_atr_mult = 1.5 * _regime_atr_mult_scale(ctx)
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, None, atr, min_atr_mult=1.5, max_atr_mult=max_atr_mult)
    if stop is None:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="H4->M15", reason=stop_reason)
    target = entry + atr * Decimal("3.0") if direction == "LONG" else entry - atr * Decimal("3.0")
    eqh_eql_side = "sell_side" if direction == "LONG" else "buy_side"
    eqh_eql_touches = _eqh_eql_touch_count(ctx, side=eqh_eql_side, price=price, atr=float(atr))
    strength = 70.0 + (20.0 if in_retracement_zone else 0.0) + (10.0 if eqh_eql_touches >= 2 else 0.0)
    evidence = {
        "htf_trend_h4": ctx.htf_trend_h4, "bos_id": latest_break.id, "displacement_confirmed": True,
        "in_fvg_or_ob_retracement_zone": in_retracement_zone, "retracement_zone_count": len(retracement_zones),
        "eqh_eql_touch_count": eqh_eql_touches,
        # New, observability-only (Section 3.6): displacement strength relative to ATR. May
        # explain part of the symbol concentration -- measured before acting, never used to
        # adjust `strength` above until its own OOS validation clears the same bar every other
        # evidence-only field in this codebase is held to.
        "displacement_magnitude_atr": strongest_displacement.magnitude_atr,
        "idm_required": _env_flag("MT5_SMC_CONTINUATION_IDM_REQUIRED", False),
        "market_regime": ctx.market_regime,
    }
    evidence.update(_squeeze_evidence(ctx))
    return _signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="H4->M15", direction=direction, strength=min(100.0, strength),
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=_geometry_metadata(ctx, entry, stop, None, atr, 1.5, max_atr_mult))
