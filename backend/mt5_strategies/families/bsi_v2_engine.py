"""BSI V2 audiovisual research engine.

All functions here are V2-only and disabled from production dispatch unless a
caller imports them directly for research/tests. V1 `bsi_engine.py` is untouched.
"""
from __future__ import annotations

import os
import threading
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from backend.market_structure.bar_utils import normalize_bars
from backend.market_structure.models import ConceptStatus, ImbalanceZone, LiquidityLevel, LiquiditySide, StructureBreak
from backend.mt5_strategies.context import StrategyContext
from backend.mt5_strategies.families.bsi_v2_interpretation import (
    build_dealing_leg,
    build_liquidity_taken_event,
    build_mentor_order_block_from_fvg,
    interpret_mentor_structure,
    liquidity_from_level,
    liquidity_from_session_level,
    liquidity_from_trendline,
)
from backend.mt5_strategies.families.bsi_v2_lifecycle import BSIDurableLifecycleStore, BSILifecycleStore, evaluate_bare_retest_freshness, evaluate_zone_entry_freshness, fixed_rr_target
from backend.mt5_strategies.families.bsi_v2_new_york import evaluate_bsi_v2_new_york
from backend.mt5_strategies.families.bsi_v2_primitives import (
    BSIEntryArray,
    BSIIdentitySeed,
    BSILifecycleState,
    BSILiquidityObject,
    BSILiquidityType,
    BSIMentorOrderBlock,
    BSIMentorStructureKind,
    BSIPremiumDiscount,
    BSIV2Evidence,
    build_bsi_entry_opportunity_id,
    build_bsi_thesis_id,
)
from backend.mt5_strategies.families.bsi_v2_scaffold import BSI_BASELINE_V2_AUDIOVISUAL
from backend.mt5_strategies.models import StrategySignal, invalid_signal

_STRATEGY_ID = "bsi_v2_research"
_FAMILY = "bsi"
_TF = "M15(bsi_v2)"
_BULLISH = "bullish"
_BEARISH = "bearish"
_BUY_SIDE = LiquiditySide.BUY_SIDE.value
_SELL_SIDE = LiquiditySide.SELL_SIDE.value
_ACTIVE_STRATEGY_ID = "bsi"
_DEMO_LIFECYCLE_LOCK = threading.Lock()
_DEMO_LIFECYCLES: dict[str, BSIDurableLifecycleStore] = {}


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _snapshot(ctx: StrategyContext) -> Any:
    return ctx.m15_snapshot


def _fixture(ctx: StrategyContext, key: str) -> dict[str, Any]:
    return dict(getattr(_snapshot(ctx), "bsi_v2_fixture", {}).get(key, {}))


def _fixture_or_raw(ctx: StrategyContext, key: str) -> dict[str, Any]:
    fx = _fixture(ctx, key)
    if fx:
        return fx
    extractor = {
        "abc": _raw_abc,
        "asian": _raw_asian,
        "under_over": _raw_under_over,
        "0930": _raw_0930,
        "reactionary": _raw_reactionary,
        "abcd": _raw_abcd,
        "ob_liquidity": _raw_ob_liquidity,
    }.get(key)
    return extractor(ctx) if extractor is not None else {}


def _bar_time(row: dict[str, Any]) -> Any:
    raw = row.get("close_time") or row.get("time") or row.get("timestamp")
    try:
        from datetime import datetime

        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None


def _tol(ctx: StrategyContext) -> Decimal:
    return (ctx.atr_m15 or Decimal("0.0005")) * Decimal("0.30")


def _fvg_after(ctx: StrategyContext, direction: str, bar_index: int) -> ImbalanceZone | None:
    zones = [
        f
        for f in _active_fvgs(ctx, direction)
        if f.supporting_bar_indexes and f.supporting_bar_indexes[-1] >= bar_index
    ]
    return max(zones, key=lambda z: z.supporting_bar_indexes[-1], default=None)


def _target_for(ctx: StrategyContext, direction: str, entry: Decimal, stop: Decimal, rr: Decimal = Decimal("2")) -> Decimal:
    return _natural_target(ctx, direction, entry) or fixed_rr_target(entry, stop, direction, rr)


def _recent_break_after(ctx: StrategyContext, direction: str, after_idx: int):
    breaks = [
        b for b in getattr(_snapshot(ctx), "breaks", [])
        if str(b.direction) == direction and int(getattr(b, "bar_index", -1)) > after_idx
    ]
    return max(breaks, key=lambda b: b.bar_index, default=None)


def _is_new_york_time(value: Any) -> bool:
    if value is None:
        return False
    return value.weekday() < 5 and ((13, 30) <= (value.hour, value.minute) < (16, 0) or (18, 0) <= (value.hour, value.minute) < (20, 0))


def _raw_asian(ctx: StrategyContext) -> dict[str, Any]:
    levels = [lvl for lvl in getattr(_snapshot(ctx), "session_levels", []) if str(getattr(lvl, "session_name", "")).lower() == "asian"]
    highs = [lvl for lvl in levels if getattr(lvl, "level_name", "") == "high"]
    lows = [lvl for lvl in levels if getattr(lvl, "level_name", "") == "low"]
    if not highs or not lows:
        return {}
    high = max(highs, key=lambda l: l.end_time)
    low = max(lows, key=lambda l: l.end_time)
    if high.end_time != low.end_time:
        return {}
    bars = _bars(ctx)
    lunch_end = high.end_time + timedelta(hours=2)
    sweeps: list[dict[str, Any]] = []
    for bar in bars:
        if bar.close_time <= high.end_time or bar.close_time > lunch_end:
            continue
        if bar.high > high.level and bar.close < high.level:
            sweeps.append({"side": "high", "idx": bar.index, "extreme": bar.high, "direction": _BEARISH})
        if bar.low < low.level and bar.close > low.level:
            sweeps.append({"side": "low", "idx": bar.index, "extreme": bar.low, "direction": _BULLISH})
    for sweep in sorted(sweeps, key=lambda item: item["idx"], reverse=True):
        brk = _recent_break_after(ctx, sweep["direction"], sweep["idx"])
        fvg = _fvg_after(ctx, sweep["direction"], brk.bar_index if brk else sweep["idx"])
        if brk is None or fvg is None:
            continue
        return {
            "structure_direction": sweep["direction"],
            "target_level_name": "low" if sweep["side"] == "high" else "high",
            "stop": str(sweep["extreme"]),
            "raw_extraction": True,
            "asian_range_found": True,
            "range_high": str(high.level),
            "range_low": str(low.level),
            "sweep_side": sweep["side"],
            "sweep_found": True,
            "mss_found": True,
            "entry_found": True,
        }
    return {}


def _raw_abc(ctx: StrategyContext) -> dict[str, Any]:
    swings = sorted(getattr(_snapshot(ctx), "swings", []), key=lambda s: s.bar_index)
    if len(swings) < 4:
        return {}
    for i in range(len(swings) - 4, -1, -1):
        a0, a1, b, c = swings[i : i + 4]
        if (a0.swing_type, a1.swing_type, b.swing_type, c.swing_type) == ("high", "low", "high", "low"):
            if b.price >= a0.price or c.price >= a1.price:
                continue
            brk = _recent_break_after(ctx, _BULLISH, c.bar_index)
            fvg = _fvg_after(ctx, _BULLISH, brk.bar_index if brk else c.bar_index)
            if brk and fvg:
                return {"structure_direction": _BULLISH, "b_leg_target": str(b.price), "stop": str(c.price), "points": [a0.id, a1.id, b.id, c.id], "raw_extraction": True}
        if (a0.swing_type, a1.swing_type, b.swing_type, c.swing_type) == ("low", "high", "low", "high"):
            if b.price <= a0.price or c.price <= a1.price:
                continue
            brk = _recent_break_after(ctx, _BEARISH, c.bar_index)
            fvg = _fvg_after(ctx, _BEARISH, brk.bar_index if brk else c.bar_index)
            if brk and fvg:
                return {"structure_direction": _BEARISH, "b_leg_target": str(b.price), "stop": str(c.price), "points": [a0.id, a1.id, b.id, c.id], "raw_extraction": True}
    return {}


def _raw_under_over(ctx: StrategyContext) -> dict[str, Any]:
    bars = _bars(ctx)
    levels = [lvl for lvl in getattr(_snapshot(ctx), "equal_levels", []) if int(getattr(lvl, "touch_count", 0)) >= 3]
    for level in sorted(levels, key=lambda l: l.confirmation_time or l.detected_time, reverse=True):
        first_idx = max(getattr(level, "supporting_bar_indexes", [0]) or [0])
        after = bars[first_idx + 1 :]
        if not after:
            continue
        if str(level.side) == _BUY_SIDE:
            breaks = [b for b in after if b.close > level.level]
            reclaims = [b for b in after if b.close < level.level]
            direction = "SHORT"
            stop = max([b.high for b in breaks], default=level.level)
        else:
            breaks = [b for b in after if b.close < level.level]
            reclaims = [b for b in after if b.close > level.level]
            direction = "LONG"
            stop = min([b.low for b in breaks], default=level.level)
        if not breaks or not reclaims:
            continue
        first_break = min(breaks, key=lambda b: b.index)
        first_reclaim = min((b for b in reclaims if b.index > first_break.index), key=lambda b: b.index, default=None)
        if first_reclaim is None:
            continue
        retested = any(
            (b.low <= level.level <= b.high) if direction == "SHORT" else (b.low <= level.level <= b.high)
            for b in after
            if b.index > first_reclaim.index
        )
        if not retested:
            continue
        entry = level.level
        target = _target_for(ctx, direction, entry, stop)
        return {"direction": direction, "level": str(entry), "target": str(target), "stop": str(stop), "touch_count": level.touch_count, "raw_extraction": True}
    return {}


def _raw_0930(ctx: StrategyContext) -> dict[str, Any]:
    symbol = ctx.symbol.upper()
    index_symbols = {"NAS100", "US100", "USTEC", "NDX", "US30", "DJI", "DJ30", "SPX500", "US500", "SP500"}
    if symbol not in index_symbols:
        return {"instrument_scope_rejection": "EXPECTED_INSTRUMENT_SCOPE"}
    return {"instrument_scope_rejection": "M1_DATA_REQUIRED"}


def _raw_reactionary(ctx: StrategyContext) -> dict[str, Any]:
    bars = _bars(ctx)
    fvgs = sorted(_active_fvgs(ctx), key=lambda z: z.supporting_bar_indexes[-1] if z.supporting_bar_indexes else -1)
    for first in fvgs:
        if not first.supporting_bar_indexes:
            continue
        touched = next((b for b in bars[first.supporting_bar_indexes[-1] + 1 :] if b.low <= first.price_high and b.high >= first.price_low), None)
        if touched is None:
            continue
        second = _fvg_after(ctx, str(first.direction), touched.index)
        if second is None or second.id == first.id:
            continue
        direction = _direction_from_structure(str(second.direction))
        stop = second.price_low if direction == "LONG" else second.price_high
        entry = (second.price_low + second.price_high) / Decimal("2")
        target = _target_for(ctx, direction, entry, stop, Decimal("3"))
        return {
            "direction": direction,
            "array1_id": first.id,
            "array2_id": second.id,
            "array2_type": "raw_fvg_after_reaction",
            "array2_low": str(second.price_low),
            "array2_high": str(second.price_high),
            "stop": str(stop),
            "target": str(target),
            "reaction_event": "mitigation_then_impulse",
            "raw_extraction": True,
        }
    return {}


def _raw_abcd(ctx: StrategyContext) -> dict[str, Any]:
    sweeps = sorted(getattr(_snapshot(ctx), "liquidity_sweeps", []), key=lambda s: s.bar_index)
    if len(sweeps) < 2:
        return {}
    for prior, latest in zip(sweeps[-6:], sweeps[-5:]):
        if prior.side == latest.side:
            continue
        direction = "LONG" if str(latest.side) == _SELL_SIDE else "SHORT"
        level = _level_by_id(ctx, latest.level_id)
        if level is None:
            continue
        return {"d_direction": direction, "p2_level": str(level.level), "stop": str(latest.swept_price), "points": [prior.id, latest.id], "raw_extraction": True}
    return {}


def _raw_ob_liquidity(ctx: StrategyContext) -> dict[str, Any]:
    bars = _bars(ctx)
    obs = sorted(getattr(_snapshot(ctx), "order_blocks", []), key=lambda ob: ob.origin_bar_index, reverse=True)
    for ob in obs:
        low = ob.price_low
        high = ob.price_high
        if low is None or high is None:
            continue
        after = bars[ob.origin_bar_index + 1 :]
        if str(ob.direction) == _BULLISH:
            heavy_clean_reaction = any(b.low <= high and b.high >= low and b.close > high for b in after[:5])
            if heavy_clean_reaction:
                continue
            fakeouts = [b for b in after if b.low < low and b.close < low]
            reclaims = [b for b in after if b.close > low]
            direction = "LONG"
            stop = min([b.low for b in fakeouts], default=low)
        else:
            heavy_clean_reaction = any(b.low <= high and b.high >= low and b.close < low for b in after[:5])
            if heavy_clean_reaction:
                continue
            fakeouts = [b for b in after if b.high > high and b.close > high]
            reclaims = [b for b in after if b.close < high]
            direction = "SHORT"
            stop = max([b.high for b in fakeouts], default=high)
        if not fakeouts or not reclaims:
            continue
        first_fakeout = min(fakeouts, key=lambda b: b.index)
        first_reclaim = min((b for b in reclaims if b.index > first_fakeout.index), key=lambda b: b.index, default=None)
        if first_reclaim is None:
            continue
        retested = any(b.low <= high and b.high >= low for b in after if b.index > first_reclaim.index)
        if not retested:
            continue
        entry = (low + high) / Decimal("2")
        target = _target_for(ctx, direction, entry, stop)
        return {"direction": direction, "origin_ob_id": ob.id, "ob_low": str(low), "ob_high": str(high), "stop": str(stop), "target": str(target), "raw_extraction": True}
    return {}


def _reject(ctx: StrategyContext, subtype: str, reason: str, evidence: dict[str, Any] | None = None) -> StrategySignal:
    signal = invalid_signal(_STRATEGY_ID, _FAMILY, symbol=ctx.symbol, broker_symbol=ctx.broker_symbol, timeframe=f"{_TF}:{subtype}", generated_at=ctx.generated_at, regime=ctx.regime, reason=reason)
    signal.evidence.update({"bsi_version": BSI_BASELINE_V2_AUDIOVISUAL, "setup_subtype": subtype})
    if evidence:
        signal.evidence.update(evidence)
    return signal


def _direction_from_structure(structure_direction: str) -> str:
    return "LONG" if structure_direction == _BULLISH else "SHORT"


def _latest_break(ctx: StrategyContext) -> StructureBreak | None:
    return max(getattr(_snapshot(ctx), "breaks", []), key=lambda b: b.bar_index, default=None)


def _active_fvgs(ctx: StrategyContext, direction: str | None = None) -> list[ImbalanceZone]:
    fvgs = [f for f in getattr(_snapshot(ctx), "imbalances", []) if str(f.status) != ConceptStatus.MITIGATED.value]
    if direction is not None:
        fvgs = [f for f in fvgs if str(f.direction) == direction]
    return fvgs


def _bars(ctx: StrategyContext):
    return normalize_bars(ctx.m15_rows, symbol=ctx.broker_symbol, timeframe="M15")


def _mentor_array(ctx: StrategyContext, fvg: ImbalanceZone, *, prefer_ob: bool = False) -> tuple[BSIEntryArray, BSIMentorOrderBlock | None]:
    bars = _bars(ctx)
    mentor_ob = build_mentor_order_block_from_fvg(fvg, bars)
    use_ob = prefer_ob or (fvg.strength is not None and float(fvg.strength) >= 0.5)
    if use_ob:
        return BSIEntryArray(mentor_ob.mentor_ob_id, "mentor_ob", mentor_ob.low, mentor_ob.high, source_fvg_id=fvg.id, source_ob_id=mentor_ob.mentor_ob_id), mentor_ob
    return BSIEntryArray(fvg.id, "fvg", fvg.price_low or Decimal("0"), fvg.price_high or Decimal("0"), source_fvg_id=fvg.id), mentor_ob


def _level_by_id(ctx: StrategyContext, level_id: str) -> LiquidityLevel | None:
    return next((lvl for lvl in getattr(_snapshot(ctx), "liquidity_levels", []) if lvl.id == level_id), None)


def _latest_sweep(ctx: StrategyContext):
    return max(getattr(_snapshot(ctx), "liquidity_sweeps", []), key=lambda s: s.bar_index, default=None)


def _natural_target(ctx: StrategyContext, direction: str, entry: Decimal) -> Decimal | None:
    levels = getattr(_snapshot(ctx), "liquidity_levels", [])
    if direction == "LONG":
        above = [lvl.level for lvl in levels if lvl.side == _BUY_SIDE and lvl.level > entry]
        return min(above) if above else None
    below = [lvl.level for lvl in levels if lvl.side == _SELL_SIDE and lvl.level < entry]
    return max(below) if below else None


def _emit(
    ctx: StrategyContext,
    *,
    subtype: str,
    direction: str,
    entry_array: BSIEntryArray | None,
    entry_level: Decimal | None = None,
    target: Decimal,
    mentor_invalidation: Decimal,
    lifecycle: BSILifecycleStore | None,
    mentor_rule_ids: tuple[str, ...],
    extra_evidence: dict[str, Any] | None = None,
    min_rr: Decimal | None = None,
) -> StrategySignal:
    lifecycle = lifecycle or BSILifecycleStore()
    seed = BSIIdentitySeed(
        subtype=subtype,
        symbol=ctx.symbol,
        direction=direction,
        timeframe="M15",
        structure_event_id=(extra_evidence or {}).get("source_break_id"),
        liquidity_id=(extra_evidence or {}).get("liquidity_id"),
        entry_array_id=entry_array.array_id if entry_array is not None else None,
        retest_level=entry_level,
    )
    thesis_id, opportunity_id, prior_state = lifecycle.ensure_detected(
        seed,
        at=ctx.generated_at,
        account_id=getattr(ctx, "account_id", None),
        replay_run_id=getattr(ctx, "replay_run_id", None),
    )
    if prior_state in {BSILifecycleState.CONSUMED, BSILifecycleState.INVALIDATED, BSILifecycleState.EXPIRED, BSILifecycleState.ENTRY_AVAILABLE}:
        return _reject(ctx, subtype, f"OPPORTUNITY_ALREADY_{prior_state.value}", {"bsi_entry_opportunity_id": opportunity_id})

    if entry_array is not None:
        freshness = evaluate_zone_entry_freshness(
            direction=direction,
            bid=ctx.bid,
            ask=ctx.ask,
            zone_low=entry_array.low,
            zone_high=entry_array.high,
            mentor_invalidation_level=mentor_invalidation,
            target=target,
            min_rr=min_rr,
            broker_min_stop_distance=ctx.broker_min_stop_distance,
            atr=ctx.atr_m15,
            spread=ctx.spread,
            min_stop_atr_mult=Decimal(str(os.getenv("BSI_V2_MIN_STOP_ATR_MULT", "1.0"))),
        )
    else:
        if entry_level is None:
            return _reject(ctx, subtype, "MISSING_ENTRY_LEVEL")
        freshness = evaluate_bare_retest_freshness(
            direction=direction,
            bid=ctx.bid,
            ask=ctx.ask,
            original_liquidity_level=entry_level,
            mentor_invalidation_level=mentor_invalidation,
            tolerance=(ctx.atr_m15 or Decimal("0.0005")) * Decimal("0.3"),
            min_rr=min_rr or Decimal("2"),
            broker_min_stop_distance=ctx.broker_min_stop_distance,
            planned_target=target,
            atr=ctx.atr_m15,
            spread=ctx.spread,
            min_stop_atr_mult=Decimal(str(os.getenv("BSI_V2_MIN_STOP_ATR_MULT", "1.0"))),
        )
    if freshness.status == "WAIT":
        return _reject(ctx, subtype, freshness.reason, {"bsi_entry_opportunity_id": opportunity_id, "freshness": freshness.reason})
    if freshness.status == "EXPIRE":
        lifecycle.mark_expired(thesis_id, opportunity_id, reason=freshness.reason, at=ctx.generated_at)
        return _reject(ctx, subtype, freshness.reason, {"bsi_entry_opportunity_id": opportunity_id, "freshness": freshness.reason})

    lifecycle.mark_available(thesis_id, opportunity_id, reason=freshness.reason, at=ctx.generated_at)
    final_stop = _dec(freshness.engineering_adjustments["final_stop"])
    evidence_model = BSIV2Evidence(
        bsi_version=BSI_BASELINE_V2_AUDIOVISUAL,
        strategy_subtype=subtype,
        mentor_rule_ids=mentor_rule_ids,
        entry_array=entry_array,
        bsi_thesis_id=thesis_id,
        bsi_entry_opportunity_id=opportunity_id,
        mentor_invalidation={"mentor_invalidation_level": str(mentor_invalidation)},
        target_semantics={"target": str(target)},
        engineering_adjustments=freshness.engineering_adjustments,
    )
    lifecycle.mark_consumed(thesis_id, opportunity_id, reason="executable_signal_emitted", at=ctx.generated_at)
    evidence = {
        **evidence_model.as_dict(),
        "setup_subtype": subtype,
        "entry_type": entry_array.array_type if entry_array is not None else "bare_retest",
        "entry_array_id": entry_array.array_id if entry_array is not None else None,
        "mentor_invalidation_level": float(mentor_invalidation),
        "final_stop": float(final_stop),
        "target": float(target),
        "freshness_status": freshness.status,
        "freshness_reason": freshness.reason,
        "lifecycle_state": BSILifecycleState.CONSUMED.value,
    }
    if extra_evidence:
        evidence.update(extra_evidence)
    return StrategySignal(
        strategy_id=_STRATEGY_ID,
        strategy_family=_FAMILY,
        symbol=ctx.symbol,
        broker_symbol=ctx.broker_symbol,
        direction=direction,
        timeframe=f"{_TF}:{subtype}",
        generated_at=ctx.generated_at,
        valid=True,
        raw_signal_strength=70.0,
        proposed_entry=float(freshness.entry_price),
        stop_loss=float(final_stop),
        take_profit=float(target),
        reward_risk=float(freshness.reward_risk) if freshness.reward_risk is not None else None,
        regime=ctx.regime,
        evidence=evidence,
        metadata={"bsi_v2_evidence": evidence_model.as_dict()},
    )


def evaluate_bsi_v2_order_flow(ctx: StrategyContext, *, lifecycle: BSILifecycleStore | None = None) -> StrategySignal:
    subtype = "bsi_order_flow"
    brk = _latest_break(ctx)
    if brk is None:
        return _reject(ctx, subtype, "NO_STRUCTURE_BREAK")
    before = _BEARISH if str(brk.break_kind) in {"mss", "choch"} and str(brk.direction) == _BULLISH else _BULLISH
    after = str(brk.direction)
    structure = interpret_mentor_structure(brk, direction_before=before, direction_after=after)
    direction = _direction_from_structure(after)

    fvg = max(_active_fvgs(ctx, after), key=lambda z: z.supporting_bar_indexes[-1], default=None)
    if fvg is None:
        return _reject(ctx, subtype, "NO_UNMITIGATED_FVG")
    entry_array, mentor_ob = _mentor_array(ctx, fvg)
    entry_mid = (entry_array.low + entry_array.high) / Decimal("2")

    pd_location = None
    if structure.mentor_classification == BSIMentorStructureKind.MSS:
        leg = build_dealing_leg(
            source_break_id=brk.id,
            start_anchor_id="break_leg_start",
            end_anchor_id="break_leg_end",
            start_price=brk.price_low or min(brk.broken_level, brk.break_price),
            end_price=brk.price_high or max(brk.broken_level, brk.break_price),
            start_bar_index=max(0, brk.bar_index - 3),
            end_bar_index=brk.bar_index,
        )
        pd_location = leg.classify(entry_mid)
        if direction == "LONG" and pd_location == BSIPremiumDiscount.PREMIUM:
            return _reject(ctx, subtype, "MSS_NOT_IN_DISCOUNT")
        if direction == "SHORT" and pd_location == BSIPremiumDiscount.DISCOUNT:
            return _reject(ctx, subtype, "MSS_NOT_IN_PREMIUM")

    trendline_liq = None
    if getattr(ctx, "trendline_pivots", None):
        line = next((tl for tl in ctx.trendline_pivots if tl.is_broken), None)
        if line is not None and len(getattr(_snapshot(ctx), "swings", [])) >= 2:
            swings = sorted(getattr(_snapshot(ctx), "swings", []), key=lambda s: s.bar_index)
            trendline_liq = liquidity_from_trendline(line, anchor_1=swings[0], anchor_2=swings[1])

    target = _natural_target(ctx, direction, entry_mid)
    if target is None:
        return _reject(ctx, subtype, "NO_NATURAL_TARGET")
    stop = entry_array.low if direction == "LONG" else entry_array.high
    return _emit(
        ctx,
        subtype=subtype,
        direction=direction,
        entry_array=entry_array,
        target=target,
        mentor_invalidation=stop,
        lifecycle=lifecycle,
        mentor_rule_ids=("BSI2-OF-001", "BSI2-OF-002", "BSI2-OB-001", "BSI2-LIQ-003", "BSI2-FRESH-001"),
        extra_evidence={
            "source_break_id": brk.id,
            "mentor_structure": structure.mentor_classification.value,
            "fvg_id": fvg.id,
            "mentor_ob_id": mentor_ob.mentor_ob_id if mentor_ob else None,
            "premium_discount": pd_location.value if pd_location else "AMBIGUOUS_OR_NOT_USED",
            "trendline_liquidity_id": trendline_liq.liquidity_id if trendline_liq else None,
            "ambiguity_flags": ["ORDER_FLOW_MSB_PD_AMBIGUOUS"] if structure.mentor_classification == BSIMentorStructureKind.MSB else [],
        },
    )


def evaluate_bsi_v2_abc(ctx: StrategyContext, *, lifecycle: BSILifecycleStore | None = None) -> StrategySignal:
    subtype = "bsi_abc"
    fx = _fixture_or_raw(ctx, "abc")
    fvg = _active_fvgs(ctx, fx.get("structure_direction", _BULLISH))[0] if _active_fvgs(ctx, fx.get("structure_direction", _BULLISH)) else None
    if not fx or fvg is None:
        return _reject(ctx, subtype, "NO_ABC_GEOMETRY")
    if fx.get("b_exceeds_a_start"):
        return _reject(ctx, subtype, "B_LEG_INVALIDATES_A")
    entry_array, mentor_ob = _mentor_array(ctx, fvg)
    direction = _direction_from_structure(fx["structure_direction"])
    return _emit(
        ctx,
        subtype=subtype,
        direction=direction,
        entry_array=entry_array,
        target=_dec(fx["b_leg_target"]),
        mentor_invalidation=_dec(fx["stop"]),
        lifecycle=lifecycle,
        mentor_rule_ids=("BSI2-ABC-001", "BSI2-ABC-003", "BSI2-ABC-004", "BSI2-ABC-005"),
        extra_evidence={"abc_points": fx.get("points"), "fvg_id": fvg.id, "mentor_ob_id": mentor_ob.mentor_ob_id, "premium_discount": "NOT_USED"},
    )


def evaluate_bsi_v2_asian(ctx: StrategyContext, *, lifecycle: BSILifecycleStore | None = None) -> StrategySignal:
    subtype = "bsi_asian"
    fx = _fixture_or_raw(ctx, "asian")
    fvg = _active_fvgs(ctx, fx.get("structure_direction", _BULLISH))[0] if fx else None
    if not fx or fvg is None:
        return _reject(ctx, subtype, "NO_ASIAN_SETUP")
    session_level = next((lvl for lvl in getattr(_snapshot(ctx), "session_levels", []) if lvl.level_name == fx.get("target_level_name")), None)
    if session_level is None:
        return _reject(ctx, subtype, "NO_OPPOSITE_ASIAN_BOX_EDGE")
    entry_array, mentor_ob = _mentor_array(ctx, fvg)
    direction = _direction_from_structure(fx["structure_direction"])
    liq = liquidity_from_session_level(session_level, side=_BUY_SIDE if direction == "LONG" else _SELL_SIDE, source_rule_ids=("BSI2-ASIAN-001",))
    return _emit(ctx, subtype=subtype, direction=direction, entry_array=entry_array, target=session_level.level, mentor_invalidation=_dec(fx["stop"]), lifecycle=lifecycle, mentor_rule_ids=("BSI2-ASIAN-001", "BSI2-ASIAN-002", "BSI2-ASIAN-003", "BSI2-ASIAN-004"), extra_evidence={"liquidity_id": liq.liquidity_id, "target_type": "asian_opposite_edge", "premium_discount": "NOT_USED", "daily_bias_required": False, "mentor_ob_id": mentor_ob.mentor_ob_id})


def evaluate_bsi_v2_under_over(ctx: StrategyContext, *, lifecycle: BSILifecycleStore | None = None) -> StrategySignal:
    subtype = "bsi_under_over"
    fx = _fixture_or_raw(ctx, "under_over")
    if not fx:
        return _reject(ctx, subtype, "NO_UNDER_OVER_LEVEL")
    if int(fx.get("touch_count", 0)) < 3:
        return _reject(ctx, subtype, "INSUFFICIENT_TOUCHES")
    if fx.get("wick_only_break"):
        return _reject(ctx, subtype, "WICK_BREAK_DOES_NOT_COUNT")
    level = _dec(fx["level"])
    direction = fx["direction"]
    target = _dec(fx["target"])
    return _emit(ctx, subtype=subtype, direction=direction, entry_array=None, entry_level=level, target=target, mentor_invalidation=_dec(fx["stop"]), lifecycle=lifecycle, mentor_rule_ids=("BSI2-UO-001", "BSI2-UO-002", "BSI2-UO-003", "BSI2-UO-004"), extra_evidence={"touch_count": fx["touch_count"], "entry_type": "bare_retest", "management_style": "partial_at_intermediate_fvgs_full_at_target", "premium_discount": "NOT_USED"})


def evaluate_bsi_v2_0930(ctx: StrategyContext, *, lifecycle: BSILifecycleStore | None = None) -> StrategySignal:
    subtype = "bsi_0930"
    fx = _fixture_or_raw(ctx, "0930")
    if not fx:
        return _reject(ctx, subtype, "NO_0930_SETUP")
    if fx.get("instrument_scope_rejection"):
        return _reject(ctx, subtype, str(fx["instrument_scope_rejection"]))
    if not fx.get("in_930_window", False):
        return _reject(ctx, subtype, "OUTSIDE_0930_WINDOW")
    if not fx.get("is_index", False):
        return _reject(ctx, subtype, "INSTRUMENT_NOT_INDEX")
    if not (fx.get("displacement_mss") or fx.get("strong_displacement_substitute")):
        return _reject(ctx, subtype, "NO_DISPLACEMENT_MSS")
    direction = _direction_from_structure(fx["structure_direction"])
    fvg = _active_fvgs(ctx, fx.get("structure_direction", _BEARISH))[0] if not fx.get("use_ob_substitute") and _active_fvgs(ctx, fx.get("structure_direction", _BEARISH)) else None
    if fx.get("use_ob_substitute"):
        low, high = _dec(fx["ob_low"]), _dec(fx["ob_high"])
        entry_array = BSIEntryArray("bsi2_930_ob_substitute", "mentor_ob_substitute", low, high)
    elif fvg is not None:
        entry_array, _ = _mentor_array(ctx, fvg)
    else:
        return _reject(ctx, subtype, "NO_0930_ENTRY_ARRAY")
    target = _dec(fx["target"])
    return _emit(ctx, subtype=subtype, direction=direction, entry_array=entry_array, target=target, mentor_invalidation=_dec(fx["stop"]), lifecycle=lifecycle, mentor_rule_ids=("BSI2-930-001", "BSI2-930-002", "BSI2-930-003", "BSI2-930-004", "BSI2-930-005", "BSI2-930-006"), extra_evidence={"target_type": "bounded_3_5", "instrument_scope": "index", "execution_timeframe": fx.get("execution_timeframe", "1m"), "premium_discount": "NOT_USED"}, min_rr=Decimal("3"))


def evaluate_bsi_v2_reactionary(ctx: StrategyContext, *, lifecycle: BSILifecycleStore | None = None) -> StrategySignal:
    subtype = "bsi_reactionary"
    fx = _fixture_or_raw(ctx, "reactionary")
    if not fx:
        return _reject(ctx, subtype, "NO_REACTIONARY_SEQUENCE")
    array2 = BSIEntryArray(fx["array2_id"], fx.get("array2_type", "ambiguous_fvg_or_ob"), _dec(fx["array2_low"]), _dec(fx["array2_high"]))
    return _emit(ctx, subtype=subtype, direction=fx["direction"], entry_array=array2, target=_dec(fx["target"]), mentor_invalidation=_dec(fx["stop"]), lifecycle=lifecycle, mentor_rule_ids=("BSI2-RB-001", "BSI2-RB-002", "BSI2-RB-003", "BSI2-RB-004"), extra_evidence={"array1_id": fx["array1_id"], "array2_id": fx["array2_id"], "reaction_event": fx.get("reaction_event"), "ambiguity_flags": ["REACTIONARY_ARRAY2_KIND_AMBIGUOUS"]})


def evaluate_bsi_v2_abcd(ctx: StrategyContext, *, lifecycle: BSILifecycleStore | None = None) -> StrategySignal:
    subtype = "bsi_abcd"
    fx = _fixture_or_raw(ctx, "abcd")
    if not fx:
        return _reject(ctx, subtype, "NO_ABCD_GEOMETRY")
    entry_level = _dec(fx["p2_level"])
    direction = fx["d_direction"]
    target = fixed_rr_target(entry_level, _dec(fx["stop"]), direction, Decimal("2"))
    return _emit(ctx, subtype=subtype, direction=direction, entry_array=None, entry_level=entry_level, target=target, mentor_invalidation=_dec(fx["stop"]), lifecycle=lifecycle, mentor_rule_ids=("BSI2-ABCD-001", "BSI2-ABCD-002", "BSI2-ABCD-003", "BSI2-ABCD-004"), extra_evidence={"abcd_points": fx.get("points"), "d_break_level": float(entry_level), "target_type": "fixed_1_2", "ambiguity_flags": ["ABCD_CLOSE_BASED_D_BREAK_AMBIGUOUS", "ABCD_OPTIONAL_1_3_TARGET_NOT_IMPLEMENTED"]})


def evaluate_bsi_v2_ob_liquidity(ctx: StrategyContext, *, lifecycle: BSILifecycleStore | None = None) -> StrategySignal:
    subtype = "bsi_ob_liquidity"
    fx = _fixture_or_raw(ctx, "ob_liquidity")
    if not fx:
        return _reject(ctx, subtype, "NO_OB_LIQUIDITY_SETUP")
    if fx.get("heavy_clean_reaction"):
        return _reject(ctx, subtype, "VALID_OB_NOT_LIQUIDITY_OB")
    if fx.get("residual_liquidity_uncleared"):
        return _reject(ctx, subtype, "RESIDUAL_LIQUIDITY_UNCLEARED")
    if fx.get("huge_fakeout"):
        return _reject(ctx, subtype, "FAKEOUT_TOO_LARGE")
    array = BSIEntryArray(fx["origin_ob_id"], "same_origin_ob", _dec(fx["ob_low"]), _dec(fx["ob_high"]), source_ob_id=fx["origin_ob_id"])
    return _emit(ctx, subtype=subtype, direction=fx["direction"], entry_array=array, target=_dec(fx["target"]), mentor_invalidation=_dec(fx["stop"]), lifecycle=lifecycle, mentor_rule_ids=("BSI2-OBL-001", "BSI2-OBL-002", "BSI2-OBL-003", "BSI2-OBL-004", "BSI2-OBL-005", "BSI2-OBL-006"), extra_evidence={"origin_ob_id": fx["origin_ob_id"], "same_array_fakeout_reclaim": True, "residual_liquidity_cleared": True, "heavy_reaction_disqualified": False})


BSI_V2_RESEARCH_EVALUATORS: dict[str, Callable[..., StrategySignal]] = {
    "bsi_order_flow": evaluate_bsi_v2_order_flow,
    "bsi_asian": evaluate_bsi_v2_asian,
    "bsi_new_york": evaluate_bsi_v2_new_york,
    "bsi_abc": evaluate_bsi_v2_abc,
    "bsi_under_over": evaluate_bsi_v2_under_over,
    "bsi_0930": evaluate_bsi_v2_0930,
    "bsi_reactionary": evaluate_bsi_v2_reactionary,
    "bsi_abcd": evaluate_bsi_v2_abcd,
    "bsi_ob_liquidity": evaluate_bsi_v2_ob_liquidity,
}


def evaluate_bsi_v2_subtype(ctx: StrategyContext, subtype: str, *, lifecycle: BSILifecycleStore | None = None) -> StrategySignal:
    evaluator = BSI_V2_RESEARCH_EVALUATORS.get(subtype)
    if evaluator is None:
        return _reject(ctx, subtype, "UNKNOWN_BSI_V2_SUBTYPE")
    return evaluator(ctx, lifecycle=lifecycle)


def evaluate_bsi_v2_research(ctx: StrategyContext, *, lifecycle: BSILifecycleStore | None = None, subtype_order: tuple[str, ...] | None = None) -> StrategySignal:
    for subtype in subtype_order or tuple(BSI_V2_RESEARCH_EVALUATORS):
        signal = evaluate_bsi_v2_subtype(ctx, subtype, lifecycle=lifecycle)
        if signal.valid:
            return signal
    return _reject(ctx, "bsi_v2_research", "NO_BSI_V2_SETUP")


def _demo_lifecycle_store(account_id: str | None = None) -> BSIDurableLifecycleStore:
    scope = (account_id or "live").strip() or "live"
    store = _DEMO_LIFECYCLES.get(scope)
    if store is None:
        path = Path(os.getenv("BSI_V2_LIFECYCLE_PATH", "data/bsi_v2_lifecycle_demo.json"))
        store = BSIDurableLifecycleStore(
            path,
            namespace=f"{BSI_BASELINE_V2_AUDIOVISUAL}:{scope}",
            replay_run_id=os.getenv("BSI_V2_LIFECYCLE_RUN_ID", "demo_runtime"),
        )
        _DEMO_LIFECYCLES[scope] = store
    return store


def evaluate_bsi_v2_active(ctx: StrategyContext) -> StrategySignal:
    """Active DEMO BSI dispatch: V2 methodology behind the existing `bsi` safety lane."""
    from backend.mt5_strategies.models import bsi_subtype_activation_status

    with _DEMO_LIFECYCLE_LOCK:
        signal = evaluate_bsi_v2_research(ctx, lifecycle=_demo_lifecycle_store(getattr(ctx, "account_id", None)))
    subtype = str(signal.evidence.get("setup_subtype") or "")
    subtype_activation = bsi_subtype_activation_status(subtype)
    evidence = {
        **signal.evidence,
        "bsi_version": BSI_BASELINE_V2_AUDIOVISUAL,
        "active_methodology": BSI_BASELINE_V2_AUDIOVISUAL,
        "subtype_activation_status": subtype_activation,
        "legacy_confidence_hard_gate": False,
        "legacy_historical_intelligence_hard_gate": False,
    }
    metadata = {**signal.metadata, "active_methodology": BSI_BASELINE_V2_AUDIOVISUAL}
    return replace(signal, strategy_id=_ACTIVE_STRATEGY_ID, strategy_family=_FAMILY, evidence=evidence, metadata=metadata)
