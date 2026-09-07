"""BSI V2 New York audiovisual evaluator.

Isolated research path only. It is not registered in production dispatch.
"""
from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from backend.market_structure.models import LiquidityLevel, LiquiditySide, LiquiditySweep
from backend.mt5_strategies.context import StrategyContext
from backend.mt5_strategies.families.bsi_v2_interpretation import build_liquidity_taken_event, liquidity_from_level
from backend.mt5_strategies.families.bsi_v2_lifecycle import BSILifecycleStore, evaluate_bare_retest_freshness, fixed_rr_target
from backend.mt5_strategies.families.bsi_v2_primitives import (
    BSIIdentitySeed,
    BSILifecycleState,
    BSILiquidityType,
    BSIV2Evidence,
)
from backend.mt5_strategies.families.bsi_v2_scaffold import BSI_BASELINE_V2_AUDIOVISUAL
from backend.mt5_strategies.models import StrategySignal, invalid_signal

_STRATEGY_ID = "bsi_v2_research"
_SUBTYPE = "bsi_new_york"
_TF = "M15(bsi_v2_new_york)"
_NY_RULE_IDS = ("BSI2-NY-001", "BSI2-NY-002", "BSI2-NY-003", "BSI2-NY-004", "BSI2-LIQ-005", "BSI2-LIFE-001", "BSI2-FRESH-001")


def _reject(ctx: StrategyContext, reason: str, evidence: dict[str, Any] | None = None) -> StrategySignal:
    signal = invalid_signal(_STRATEGY_ID, "bsi", symbol=ctx.symbol, broker_symbol=ctx.broker_symbol, timeframe=_TF, generated_at=ctx.generated_at, regime=ctx.regime, reason=reason)
    if evidence:
        signal.evidence.update(evidence)
    return signal


def _level_by_id(levels: list[LiquidityLevel], level_id: str) -> LiquidityLevel | None:
    return next((level for level in levels if level.id == level_id), None)


def _ny_sweep(ctx: StrategyContext) -> LiquiditySweep | None:
    sweeps = sorted((s for s in ctx.m15_snapshot.liquidity_sweeps if _is_new_york_time(s.confirmation_time or s.end_time)), key=lambda s: s.bar_index, reverse=True)
    return sweeps[0] if sweeps else None


def _is_new_york_time(value: Any) -> bool:
    if value is None:
        return False
    return value.weekday() < 5 and ((13, 30) <= (value.hour, value.minute) < (16, 0) or (18, 0) <= (value.hour, value.minute) < (20, 0))


def _touched_original_level_after_sweep(ctx: StrategyContext, *, sweep: LiquiditySweep, original_level: Decimal) -> tuple[int, Decimal] | None:
    for idx, row in enumerate(ctx.m15_rows):
        if idx <= sweep.bar_index:
            continue
        low = Decimal(str(row["low"]))
        high = Decimal(str(row["high"]))
        if low <= original_level <= high:
            return idx, original_level
    return None


def evaluate_bsi_v2_new_york(ctx: StrategyContext, *, lifecycle: BSILifecycleStore | None = None, mark_consumed: bool = True) -> StrategySignal:
    lifecycle = lifecycle or BSILifecycleStore()
    sweep = _ny_sweep(ctx)
    if sweep is None:
        return _reject(ctx, "NO_NY_SWING_SWEEP", {"bsi_version": BSI_BASELINE_V2_AUDIOVISUAL})

    level = _level_by_id(ctx.m15_snapshot.liquidity_levels, sweep.level_id)
    if level is None:
        return _reject(ctx, "SWEEP_ORIGINAL_LEVEL_NOT_FOUND", {"bsi_version": BSI_BASELINE_V2_AUDIOVISUAL, "sweep_id": sweep.id, "sweep_level_id": sweep.level_id})
    if str(level.source).lower() not in {"swing", "structural_swing", "mentor_swing", "confirmed_swing"}:
        return _reject(ctx, "NY_LIQUIDITY_NOT_SIGNIFICANT_SWING", {"bsi_version": BSI_BASELINE_V2_AUDIOVISUAL, "liquidity_source": level.source})

    direction = "SHORT" if sweep.side == LiquiditySide.BUY_SIDE.value else "LONG"
    original_level = level.level
    retest = _touched_original_level_after_sweep(ctx, sweep=sweep, original_level=original_level)
    if retest is None:
        return _reject(
            ctx,
            "ORIGINAL_LEVEL_NOT_RETESTED",
            {
                "bsi_version": BSI_BASELINE_V2_AUDIOVISUAL,
                "liquidity_level": float(original_level),
                "sweep_wick_extreme": float(sweep.swept_price),
            },
        )

    liquidity = liquidity_from_level(level, liquidity_type=BSILiquidityType.SWING_LEVEL, source_rule_ids=("BSI2-NY-001", "BSI2-NY-002"))
    taken = build_liquidity_taken_event(
        liquidity,
        sweep_extreme=sweep.swept_price,
        event_bar_index=sweep.bar_index,
        event_time=sweep.confirmation_time or sweep.end_time,
        source_rule_ids=("BSI2-LIQ-005", "BSI2-NY-002"),
    )
    retest_bar_index, retest_price = retest
    seed = BSIIdentitySeed(
        subtype=_SUBTYPE,
        symbol=ctx.symbol,
        direction=direction,
        timeframe="M15",
        liquidity_id=liquidity.liquidity_id,
        retest_level=retest_price,
    )
    thesis_id, opportunity_id, prior_state = lifecycle.ensure_detected(seed, at=ctx.generated_at)
    if prior_state in {BSILifecycleState.CONSUMED, BSILifecycleState.INVALIDATED, BSILifecycleState.EXPIRED, BSILifecycleState.ENTRY_AVAILABLE}:
        return _reject(ctx, f"OPPORTUNITY_ALREADY_{prior_state.value}", {"bsi_version": BSI_BASELINE_V2_AUDIOVISUAL, "bsi_entry_opportunity_id": opportunity_id})

    atr = ctx.atr_m15 or Decimal("0.0005")
    tolerance = atr * Decimal("0.3")
    mentor_invalidation = sweep.swept_price
    planned_entry = original_level
    planned_target = fixed_rr_target(planned_entry, mentor_invalidation, direction, Decimal("2"))
    freshness = evaluate_bare_retest_freshness(
        direction=direction,
        bid=ctx.bid,
        ask=ctx.ask,
        original_liquidity_level=original_level,
        mentor_invalidation_level=mentor_invalidation,
        tolerance=tolerance,
        min_rr=Decimal("2"),
        broker_min_stop_distance=ctx.broker_min_stop_distance,
        planned_target=planned_target,
        atr=ctx.atr_m15,
        spread=ctx.spread,
        min_stop_atr_mult=Decimal(str(os.getenv("BSI_V2_MIN_STOP_ATR_MULT", "1.0"))),
    )
    if freshness.status == "WAIT":
        return _reject(ctx, freshness.reason, {"bsi_version": BSI_BASELINE_V2_AUDIOVISUAL, "bsi_entry_opportunity_id": opportunity_id, "freshness": freshness.reason})
    if freshness.status == "EXPIRE":
        lifecycle.mark_expired(thesis_id, opportunity_id, reason=freshness.reason, at=ctx.generated_at)
        return _reject(ctx, freshness.reason, {"bsi_version": BSI_BASELINE_V2_AUDIOVISUAL, "bsi_entry_opportunity_id": opportunity_id, "freshness": freshness.reason})

    lifecycle.mark_available(thesis_id, opportunity_id, reason=freshness.reason, at=ctx.generated_at)
    final_stop = Decimal(str(freshness.engineering_adjustments.get("final_stop")))
    evidence_model = BSIV2Evidence(
        bsi_version=BSI_BASELINE_V2_AUDIOVISUAL,
        strategy_subtype=_SUBTYPE,
        mentor_rule_ids=_NY_RULE_IDS,
        liquidity=liquidity,
        liquidity_taken=taken,
        bsi_thesis_id=thesis_id,
        bsi_entry_opportunity_id=opportunity_id,
        mentor_invalidation={"mentor_invalidation_level": str(mentor_invalidation)},
        target_semantics={"type": "fixed_rr", "rr": "2"},
        engineering_adjustments=freshness.engineering_adjustments,
    )
    if mark_consumed:
        lifecycle.mark_consumed(thesis_id, opportunity_id, reason="executable_signal_emitted", at=ctx.generated_at)
    evidence = {
        **evidence_model.as_dict(),
        "setup_subtype": _SUBTYPE,
        "entry_type": "bare_retest",
        "liquidity_id": liquidity.liquidity_id,
        "source_liquidity_level_id": level.id,
        "liquidity_level": float(original_level),
        "sweep_wick_extreme": float(sweep.swept_price),
        "retest_bar_index": retest_bar_index,
        "retest_price": float(retest_price),
        "entry_price": float(freshness.entry_price) if freshness.entry_price is not None else None,
        "mentor_invalidation_level": float(mentor_invalidation),
        "raw_structural_stop": float(mentor_invalidation),
        "engineering_min_stop": None,
        "broker_min_stop": float(ctx.broker_min_stop_distance) if ctx.broker_min_stop_distance is not None else None,
        "final_stop": float(final_stop),
        "constraint_source": freshness.engineering_adjustments.get("constraint_source"),
        "freshness_status": freshness.status,
        "freshness_reason": freshness.reason,
        "lifecycle_state": BSILifecycleState.CONSUMED.value if mark_consumed else BSILifecycleState.ENTRY_AVAILABLE.value,
    }
    return StrategySignal(
        strategy_id=_STRATEGY_ID,
        strategy_family="bsi",
        symbol=ctx.symbol,
        broker_symbol=ctx.broker_symbol,
        direction=direction,
        timeframe=_TF,
        generated_at=ctx.generated_at,
        valid=True,
        raw_signal_strength=68.0,
        proposed_entry=float(freshness.entry_price),
        stop_loss=float(final_stop),
        take_profit=float(freshness.target),
        reward_risk=float(freshness.reward_risk),
        regime=ctx.regime,
        evidence=evidence,
        metadata={"bsi_v2_evidence": evidence_model.as_dict()},
    )
