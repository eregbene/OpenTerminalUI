"""BSI V2 mentor interpretation helpers over raw market_structure objects."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from backend.market_structure.bar_utils import StructureBar
from backend.market_structure.models import ImbalanceZone, LiquidityLevel, SessionLevel, StructureBreak, SwingPoint, stable_id
from backend.market_structure.pivot_trendlines import TrendlinePivotSummary
from backend.mt5_strategies.families.bsi_v2_primitives import (
    BSIDealingLeg,
    BSILiquidityObject,
    BSILiquidityStatus,
    BSILiquidityTakenEvent,
    BSILiquidityType,
    BSIMentorFVGRelation,
    BSIMentorOrderBlock,
    BSIMentorStructureEvent,
    BSIMentorStructureKind,
    BSITrendlineGeometry,
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def interpret_mentor_structure(
    raw_break: StructureBreak,
    *,
    direction_before: str,
    direction_after: str,
    source_rule_ids: Iterable[str] = ("BSI2-STRUCT-001", "BSI2-STRUCT-002"),
) -> BSIMentorStructureEvent:
    """Map raw break evidence to mentor MSB/MSS only.

    BSI V2 does not expose CHoCH as a separate mentor methodology state.
    Same before/after direction means MSB; changed direction means MSS.
    """
    before = direction_before.lower()
    after = direction_after.lower()
    mentor_kind = BSIMentorStructureKind.MSB if before == after else BSIMentorStructureKind.MSS
    return BSIMentorStructureEvent(
        source_break_id=raw_break.id,
        direction_before=before,
        direction_after=after,
        mentor_classification=mentor_kind,
        relevant_swing_id=raw_break.broken_swing_id,
        break_level=raw_break.broken_level,
        break_price=raw_break.break_price,
        break_bar_index=raw_break.bar_index,
        break_time=_utc(raw_break.confirmation_time or raw_break.end_time),
        source_rule_ids=tuple(source_rule_ids),
    )


def build_mentor_fvg_relation(fvg: ImbalanceZone, *, source_rule_ids: Iterable[str] = ("BSI2-FVG-001",)) -> BSIMentorFVGRelation:
    indexes = list(fvg.supporting_bar_indexes)
    if len(indexes) != 3:
        raise ValueError("mentor FVG relation requires the raw 3-candle supporting indexes")
    return BSIMentorFVGRelation(
        fvg_id=fvg.id,
        candle_1_index=indexes[0],
        impulse_candle_index=indexes[1],
        candle_3_index=indexes[2],
        low=fvg.price_low or Decimal("0"),
        high=fvg.price_high or Decimal("0"),
        direction=str(fvg.direction),
        mitigation_state=str(fvg.status),
        source_rule_ids=tuple(source_rule_ids),
    )


def build_mentor_order_block_from_fvg(
    fvg: ImbalanceZone,
    bars: list[StructureBar],
    *,
    dealing_zone_relationship: str | None = None,
    rejected_competing_ob_ids: Iterable[str] = (),
) -> BSIMentorOrderBlock:
    relation = build_mentor_fvg_relation(fvg)
    first_idx = relation.candle_1_index
    if first_idx < 0 or first_idx >= len(bars):
        raise ValueError("FVG candle 1 index is outside bar history")
    candle = bars[first_idx]
    low = min(candle.open, candle.close, candle.low)
    high = max(candle.open, candle.close, candle.high)
    mentor_ob_id = stable_id("bsi2_mob", fvg.symbol, fvg.timeframe, fvg.id, first_idx, low, high, fvg.direction)
    return BSIMentorOrderBlock(
        mentor_ob_id=mentor_ob_id,
        fvg_id=fvg.id,
        fvg_relation=relation,
        selected_ob_candle_index=first_idx,
        low=low,
        high=high,
        direction=str(fvg.direction),
        dealing_zone_relationship=dealing_zone_relationship,
        reason_selected="FVG_ANCHORED_FIRST_CANDLE",
        rejected_competing_ob_ids=tuple(rejected_competing_ob_ids),
    )


def liquidity_from_level(level: LiquidityLevel, *, liquidity_type: BSILiquidityType, source_rule_ids: Iterable[str]) -> BSILiquidityObject:
    return BSILiquidityObject(
        liquidity_id=stable_id("bsi2_liq", level.id, liquidity_type, level.level),
        liquidity_type=liquidity_type,
        side=str(level.side),
        source_timeframe=level.timeframe,
        creation_time=_utc(level.detected_time),
        source_swing_ids=tuple(level.supporting_event_ids),
        geometry={"level": str(level.level), "tolerance": str(level.tolerance)},
        reference_level=level.level,
        source_rule_ids=tuple(source_rule_ids),
    )


def liquidity_from_session_level(level: SessionLevel, *, side: str, source_rule_ids: Iterable[str]) -> BSILiquidityObject:
    return BSILiquidityObject(
        liquidity_id=stable_id("bsi2_liq_session", level.id, level.session_name, level.level_name, level.level),
        liquidity_type=BSILiquidityType.SESSION_BOX_EDGE,
        side=side,
        source_timeframe=level.timeframe,
        creation_time=_utc(level.detected_time),
        geometry={"session_name": level.session_name, "level_name": level.level_name, "level": str(level.level)},
        reference_level=level.level,
        source_rule_ids=tuple(source_rule_ids),
    )


def liquidity_from_trendline(
    trendline: TrendlinePivotSummary,
    *,
    anchor_1: SwingPoint,
    anchor_2: SwingPoint,
    active_until_bar_index: int | None = None,
    source_rule_ids: Iterable[str] = ("BSI2-LIQ-003",),
) -> BSILiquidityObject:
    slope = Decimal(str(trendline.slope))
    intercept = Decimal(str(trendline.intercept))
    geometry = BSITrendlineGeometry(
        anchor_1_id=anchor_1.id,
        anchor_2_id=anchor_2.id,
        anchor_1_bar_index=anchor_1.bar_index,
        anchor_2_bar_index=anchor_2.bar_index,
        anchor_1_price=anchor_1.price,
        anchor_2_price=anchor_2.price,
        slope=slope,
        intercept=intercept,
        active_from_bar_index=anchor_2.bar_index,
        active_until_bar_index=active_until_bar_index,
    )
    current_level = geometry.projected_value(trendline.break_bar_index or trendline.end_bar_index)
    return BSILiquidityObject(
        liquidity_id=stable_id("bsi2_tlq", trendline.id, anchor_1.id, anchor_2.id),
        liquidity_type=BSILiquidityType.TRENDLINE_LIQUIDITY,
        side="buy_side" if trendline.swing_type == "high" else "sell_side",
        source_timeframe=trendline.timeframe,
        creation_time=_utc(anchor_2.detected_time),
        source_swing_ids=(anchor_1.id, anchor_2.id),
        geometry={
            "anchor_1_id": geometry.anchor_1_id,
            "anchor_2_id": geometry.anchor_2_id,
            "slope": str(geometry.slope),
            "intercept": str(geometry.intercept),
            "touch_count": trendline.touch_count,
            "is_broken": trendline.is_broken,
            "break_bar_index": trendline.break_bar_index,
        },
        reference_level=current_level,
        status=BSILiquidityStatus.TAKEN if trendline.is_broken else BSILiquidityStatus.ACTIVE,
        taken=trendline.is_broken,
        source_rule_ids=tuple(source_rule_ids),
    )


def build_liquidity_taken_event(
    liquidity: BSILiquidityObject,
    *,
    sweep_extreme: Decimal,
    event_bar_index: int,
    event_time: datetime,
    projected_level: Decimal | None = None,
    source_rule_ids: Iterable[str] = ("BSI2-LIQ-005",),
) -> BSILiquidityTakenEvent:
    original = projected_level if projected_level is not None else liquidity.reference_level
    if original is None:
        raise ValueError("liquidity taken event requires an original/projected level")
    return BSILiquidityTakenEvent(
        liquidity_id=liquidity.liquidity_id,
        liquidity_type=liquidity.liquidity_type,
        original_liquidity_level=original,
        sweep_extreme=sweep_extreme,
        event_bar_index=event_bar_index,
        event_time=_utc(event_time),
        side=liquidity.side,
        source_rule_ids=tuple(source_rule_ids),
    )


def build_dealing_leg(
    *,
    source_break_id: str,
    start_anchor_id: str,
    end_anchor_id: str,
    start_price: Decimal,
    end_price: Decimal,
    start_bar_index: int,
    end_bar_index: int,
    source_rule_ids: Iterable[str] = ("BSI2-PD-001", "BSI2-PD-002"),
) -> BSIDealingLeg:
    return BSIDealingLeg(
        leg_id=stable_id("bsi2_leg", source_break_id, start_anchor_id, end_anchor_id, start_price, end_price),
        source_break_id=source_break_id,
        start_anchor_id=start_anchor_id,
        end_anchor_id=end_anchor_id,
        start_price=start_price,
        end_price=end_price,
        start_bar_index=start_bar_index,
        end_bar_index=end_bar_index,
        source_rule_ids=tuple(source_rule_ids),
    )

