from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.market_data.models import AssetClass, DataQualityMetadata
from backend.market_structure import MarketStructureEngine, get_profile
from backend.market_structure.bar_utils import normalize_bars
from backend.strategies.indicators import compute_indicators
from backend.strategies.models import StrategyContext, StrategySpec


def build_context(
    spec: StrategySpec,
    bars: list[dict[str, Any]],
    *,
    symbol: str,
    instrument_id: str | None = None,
    asset_class: AssetClass = AssetClass.UNKNOWN,
    dataset_snapshot_id: str | None = None,
    data_quality: DataQualityMetadata | None = None,
) -> StrategyContext:
    completed = [row for row in bars if row.get("is_complete", True)]
    if not completed:
        raise ValueError("at least one completed bar is required")
    current = completed[-1]
    ts = current.get("close_time") or current.get("timestamp") or current.get("time")
    if isinstance(ts, datetime):
        as_of = ts
    elif isinstance(ts, (int, float)):
        as_of = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    else:
        as_of = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    features = extract_price_features(completed)
    indicators = compute_indicators(completed)
    features.update(indicators)
    structure_snapshot = MarketStructureEngine(get_profile("internal")).analyze(
        normalize_bars(completed, symbol=symbol, timeframe=spec.timeframes.execution),
        symbol=symbol,
        timeframe=spec.timeframes.execution,
        instrument_id=instrument_id or symbol,
        asset_class=asset_class,
        source_dataset_id=dataset_snapshot_id,
    )
    features.update(extract_structure_features(structure_snapshot.model_dump(mode="json"), completed))
    return StrategyContext(
        instrument_id=instrument_id or symbol,
        symbol=symbol,
        asset_class=asset_class,
        execution_timeframe=spec.timeframes.execution,
        context_timeframes=spec.timeframes.context,
        as_of_timestamp=as_of,
        completed_bars=completed,
        current_bar=current,
        indicator_values=indicators,
        features=features,
        feature_timestamps={key: as_of for key in features},
        market_structure_snapshot=structure_snapshot.model_dump(mode="json"),
        data_quality=data_quality,
        dataset_snapshot_id=dataset_snapshot_id,
        strategy_version=spec.strategy.version,
        strategy_hash=spec.strategy_hash(),
    )


def extract_price_features(bars: list[dict[str, Any]]) -> dict[str, Any]:
    current = bars[-1]
    prev = bars[-2] if len(bars) > 1 else current
    return {
        "price.open": float(current["open"]),
        "price.high": float(current["high"]),
        "price.low": float(current["low"]),
        "price.close": float(current["close"]),
        "price.volume": float(current.get("volume") or 0),
        "price.previous_close": float(prev["close"]),
        "price.change": float(current["close"]) - float(prev["close"]),
        "price.is_bullish": float(current["close"]) >= float(current["open"]),
        "price.is_bearish": float(current["close"]) <= float(current["open"]),
    }


def extract_structure_features(snapshot: dict[str, Any], bars: list[dict[str, Any]]) -> dict[str, Any]:
    features: dict[str, Any] = {}
    trend = snapshot.get("trend") or {}
    features["structure.trend"] = trend.get("state")
    breaks = snapshot.get("breaks") or []
    bos = [row for row in breaks if row.get("break_kind") == "bos"]
    choch = [row for row in breaks if row.get("break_kind") == "choch"]
    mss = [row for row in breaks if row.get("break_kind") == "mss"]
    latest_break = breaks[-1] if breaks else {}
    features["structure.latest_break.type"] = latest_break.get("break_kind")
    features["structure.latest_break.direction"] = latest_break.get("direction")
    features["structure.last_bos.direction"] = bos[-1].get("direction") if bos else None
    features["structure.last_choch.direction"] = choch[-1].get("direction") if choch else None
    features["structure.last_mss.direction"] = mss[-1].get("direction") if mss else None
    features["structure.bars_since_break"] = (len(bars) - 1 - int(latest_break.get("bar_index", len(bars) - 1))) if latest_break else None
    sweeps = snapshot.get("liquidity_sweeps") or []
    latest_sweep = sweeps[-1] if sweeps else {}
    features["liquidity.latest_sweep.direction"] = latest_sweep.get("side")
    features["liquidity.sweep.direction"] = latest_sweep.get("side")
    features["liquidity.sweep.price"] = _float_or_none(latest_sweep.get("swept_price"))
    levels = snapshot.get("liquidity_levels") or []
    buy_levels = [_float_or_none(row.get("level")) for row in levels if row.get("side") == "buy_side"]
    sell_levels = [_float_or_none(row.get("level")) for row in levels if row.get("side") == "sell_side"]
    close = float(bars[-1]["close"])
    feature_rows = snapshot.get("features") or [{}]
    displacement_rows = snapshot.get("displacements") or [{}]
    atr = _float_or_none(feature_rows[-1].get("displacement_score")) or _float_or_none(displacement_rows[-1].get("magnitude_atr")) or 1.0
    features["liquidity.nearest_buy_side.level"] = min([v for v in buy_levels if v is not None and v >= close], default=None)
    features["liquidity.nearest_sell_side.level"] = max([v for v in sell_levels if v is not None and v <= close], default=None)
    features["liquidity.nearest_buy_side.distance_atr"] = _distance(features["liquidity.nearest_buy_side.level"], close, atr)
    features["liquidity.nearest_sell_side.distance_atr"] = _distance(features["liquidity.nearest_sell_side.level"], close, atr)
    fvg = snapshot.get("imbalances") or []
    features["fvg.inside_bullish"] = any(row.get("direction") == "bullish" and _inside(close, row) for row in fvg)
    features["fvg.inside_bearish"] = any(row.get("direction") == "bearish" and _inside(close, row) for row in fvg)
    obs = snapshot.get("order_blocks") or []
    features["order_block.inside_bullish"] = any(row.get("direction") == "bullish" and _inside(close, row) for row in obs)
    features["order_block.inside_bearish"] = any(row.get("direction") == "bearish" and _inside(close, row) for row in obs)
    ranges = snapshot.get("dealing_ranges") or []
    features["dealing_range.position"] = ranges[-1].get("normalized_current_position") if ranges else None
    sessions = snapshot.get("session_levels") or []
    features["session.name"] = sessions[-1].get("session_name") if sessions else None
    features["market_data.quality_score"] = 1.0
    features["market_data.freshness"] = "fresh"
    features["market_data.is_simulated"] = False
    return features


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _inside(close: float, zone: dict[str, Any]) -> bool:
    low = _float_or_none(zone.get("price_low"))
    high = _float_or_none(zone.get("price_high"))
    return low is not None and high is not None and low <= close <= high


def _distance(level: float | None, close: float, atr: float) -> float | None:
    if level is None:
        return None
    return abs(level - close) / max(abs(atr), 0.0000001)
