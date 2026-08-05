from __future__ import annotations

from decimal import Decimal

from backend.market_structure.bar_utils import StructureBar, average_true_range
from backend.market_structure.configuration import MarketStructureConfig
from backend.market_structure.models import ConceptStatus, Direction, SwingPoint, stable_id


def detect_swings(
    bars: list[StructureBar],
    config: MarketStructureConfig,
    *,
    symbol: str,
    timeframe: str,
    source_dataset_id: str | None = None,
) -> list[SwingPoint]:
    cfg = config.swings
    if config.input.use_completed_bars_only:
        bars = [bar for bar in bars if bar.is_complete or config.input.allow_incomplete_last_bar]
    min_len = cfg.left_bars + cfg.right_bars + 1
    if len(bars) < min_len:
        return []

    atrs = average_true_range(bars, config.displacement.atr_period)
    swings: list[SwingPoint] = []
    last_by_type: dict[str, SwingPoint] = {}
    for idx in range(cfg.left_bars, len(bars) - cfg.right_bars):
        bar = bars[idx]
        left = bars[idx - cfg.left_bars : idx]
        right = bars[idx + 1 : idx + cfg.right_bars + 1]
        atr = atrs[idx] or Decimal("0")
        candidates: list[tuple[str, Decimal, Direction]] = []
        if bar.high >= max(row.high for row in left) and bar.high >= max(row.high for row in right):
            candidates.append(("high", bar.high, Direction.BEARISH))
        if bar.low <= min(row.low for row in left) and bar.low <= min(row.low for row in right):
            candidates.append(("low", bar.low, Direction.BULLISH))

        for swing_type, price, direction in candidates:
            prev = last_by_type.get(swing_type)
            if prev is not None:
                separation = idx - prev.bar_index
                price_move = abs(price - prev.price)
                min_atr_move = atr * Decimal(str(cfg.minimum_atr))
                if separation < cfg.minimum_separation_bars:
                    if (swing_type == "high" and price <= prev.price) or (swing_type == "low" and price >= prev.price):
                        continue
                    swings = [item for item in swings if item.id != prev.id]
                elif price_move < Decimal(str(cfg.minimum_price_movement)) or price_move < min_atr_move:
                    continue

            confirmation_bar = bars[idx + cfg.right_bars]
            swing = SwingPoint(
                id=stable_id("swg", symbol, timeframe, swing_type, idx, price, config.configuration_hash()),
                symbol=symbol,
                timeframe=timeframe,
                start_time=bar.open_time,
                end_time=bar.close_time,
                detected_time=confirmation_bar.close_time,
                confirmation_time=confirmation_bar.close_time,
                price_low=price,
                price_high=price,
                direction=direction,
                status=ConceptStatus.CONFIRMED,
                strength=float((atr and abs(price - (prev.price if prev else price)) / atr) or 0),
                quality_score=1.0,
                configuration_version=config.version,
                configuration_hash=config.configuration_hash(),
                source_dataset_id=source_dataset_id,
                supporting_bar_indexes=list(range(idx - cfg.left_bars, idx + cfg.right_bars + 1)),
                swing_type=swing_type,
                bar_index=idx,
                price=price,
                candidate_time=bar.close_time,
                metadata={"confirmation_delay_bars": cfg.right_bars, "method": cfg.method},
            )
            swings.append(swing)
            last_by_type[swing_type] = swing
    swings.sort(key=lambda item: (item.bar_index, item.swing_type))
    return swings
