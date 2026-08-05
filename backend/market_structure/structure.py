from __future__ import annotations

from decimal import Decimal

from backend.market_structure.bar_utils import StructureBar, average_true_range
from backend.market_structure.configuration import BreakConfirmation, MarketStructureConfig
from backend.market_structure.models import (
    ConceptStatus,
    Direction,
    DisplacementEvent,
    StructureBreak,
    StructureBreakKind,
    SwingPoint,
    TrendLabel,
    TrendState,
    stable_id,
)


def _break_price(bar: StructureBar, direction: Direction, mode: BreakConfirmation) -> Decimal:
    if mode == BreakConfirmation.WICK:
        return bar.high if direction == Direction.BULLISH else bar.low
    if mode == BreakConfirmation.BODY_CLOSE:
        return max(bar.open, bar.close) if direction == Direction.BULLISH else min(bar.open, bar.close)
    return bar.close


def detect_structure_breaks(
    bars: list[StructureBar],
    swings: list[SwingPoint],
    trend: TrendState,
    displacements: list[DisplacementEvent],
    config: MarketStructureConfig,
    *,
    symbol: str,
    timeframe: str,
    source_dataset_id: str | None = None,
) -> list[StructureBreak]:
    if not swings:
        return []
    atrs = average_true_range(bars, config.displacement.atr_period)
    displacement_by_bar = {event.bar_index: event for event in displacements}
    breaks: list[StructureBreak] = []
    broken: set[str] = set()
    swing_cursor = 0
    active_high: SwingPoint | None = None
    active_low: SwingPoint | None = None
    ordered_swings = sorted(swings, key=lambda item: (item.confirmation_time or item.detected_time, item.bar_index))
    for idx, bar in enumerate(bars):
        while swing_cursor < len(ordered_swings):
            swing = ordered_swings[swing_cursor]
            if not swing.confirmation_time or swing.confirmation_time > bar.close_time:
                break
            if swing.bar_index < idx:
                if swing.swing_type == "high":
                    active_high = swing
                elif swing.swing_type == "low":
                    active_low = swing
            swing_cursor += 1
        if active_high is None and active_low is None:
            continue
        candidates = []
        if active_high is not None:
            candidates.append((active_high, Direction.BULLISH, active_high.price))
        if active_low is not None:
            candidates.append((active_low, Direction.BEARISH, active_low.price))
        for swing, direction, level in candidates:
            key = f"{swing.id}:{direction}"
            if key in broken:
                continue
            price = _break_price(bar, direction, BreakConfirmation(config.structure.break_confirmation))
            breached = price > level if direction == Direction.BULLISH else price < level
            if not breached:
                continue
            atr = atrs[idx]
            distance = abs(price - level)
            distance_atr = float(distance / atr) if atr and atr > 0 else None
            if distance_atr is not None and distance_atr < config.structure.minimum_break_atr:
                continue
            displacement = displacement_by_bar.get(idx)
            if config.structure.require_displacement and not displacement:
                continue
            if config.structure.break_confirmation == BreakConfirmation.CLOSE_PLUS_DISPLACEMENT and not displacement:
                continue
            kind = StructureBreakKind.UNCLASSIFIED
            if trend.state == TrendLabel.BULLISH:
                kind = StructureBreakKind.BOS if direction == Direction.BULLISH else StructureBreakKind.MSS if displacement else StructureBreakKind.CHOCH
            elif trend.state == TrendLabel.BEARISH:
                kind = StructureBreakKind.BOS if direction == Direction.BEARISH else StructureBreakKind.MSS if displacement else StructureBreakKind.CHOCH
            elif not config.structure.choch_requires_prior_trend:
                kind = StructureBreakKind.BOS
            explanation = (
                f"{kind.value.upper()} confirmed because the {timeframe} bar closed at {bar.close} "
                f"against level {level} from swing {swing.id} using {config.structure.break_confirmation} confirmation."
            )
            item = StructureBreak(
                id=stable_id("brk", symbol, timeframe, idx, swing.id, kind, config.configuration_hash()),
                symbol=symbol,
                timeframe=timeframe,
                start_time=swing.start_time,
                end_time=bar.close_time,
                detected_time=bar.close_time,
                confirmation_time=bar.close_time,
                price_low=min(level, price),
                price_high=max(level, price),
                direction=direction,
                status=ConceptStatus.CONFIRMED,
                strength=distance_atr,
                quality_score=min(1.0, (distance_atr or 0.0) / 2.0 + (0.25 if displacement else 0)),
                configuration_version=config.version,
                configuration_hash=config.configuration_hash(),
                source_dataset_id=source_dataset_id,
                supporting_bar_indexes=[swing.bar_index, idx],
                supporting_event_ids=[swing.id] + ([displacement.id] if displacement else []),
                break_kind=kind,
                broken_swing_id=swing.id,
                broken_level=level,
                break_price=price,
                break_distance=distance,
                break_distance_atr=distance_atr,
                confirmation_mode=config.structure.break_confirmation,
                continuation_direction=direction if kind == StructureBreakKind.BOS else Direction.UNKNOWN,
                bar_index=idx,
                explanation=explanation,
            )
            breaks.append(item)
            broken.add(key)
    breaks.sort(key=lambda item: (item.bar_index, item.break_kind))
    return breaks
