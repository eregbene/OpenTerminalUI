from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean
from typing import Any

from sqlalchemy.orm import Session

from backend.forex_intelligence.feature_store import FEATURE_SCHEMA_VERSION, INTELLIGENCE_ENGINE_VERSION, feature_store
from backend.forex_intelligence.indicators import adx_proxy, atr_values, ema, historical_volatility, macd, regression_slope, roc, rsi_values, stochastic_rsi
from backend.forex_intelligence.instruments import SUPPORTED_TIMEFRAMES, ForexInstrument, get_forex_instrument, normalize_forex_symbol, provider_symbol
from backend.forex_intelligence.models import ForexFeatureVector, ForexIndicatorSnapshot, ForexIntelligenceSnapshot
from backend.forex_intelligence.orm import ForexFeatureVectorORM
from backend.forex_intelligence.sessions import session_name, session_range
from backend.market_data.models import AssetClass
from backend.market_structure import MarketStructureEngine, get_profile
from backend.market_structure.bar_utils import StructureBar, normalize_bars
from backend.market_structure.models import stable_id


class ForexIntelligenceService:
    def analyze_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        symbol: str = "EURUSD",
        timeframe: str = "1h",
        db: Session | None = None,
        source_provider: str = "provided",
        source_dataset_id: str | None = None,
        provider_symbol_value: str | None = None,
    ) -> ForexIntelligenceSnapshot:
        normalized_symbol = _normalize_symbol(symbol)
        if timeframe not in SUPPORTED_TIMEFRAMES:
            raise ValueError(f"unsupported forex timeframe: {timeframe}")
        instrument = get_forex_instrument(normalized_symbol)
        bars = normalize_bars(rows, symbol=normalized_symbol, timeframe=timeframe)
        if len(bars) < 30:
            raise ValueError("at least 30 completed bars are required")
        dataset_id = source_dataset_id or f"forex-intelligence:{normalized_symbol}:{timeframe}:{len(bars)}"
        structure = MarketStructureEngine(get_profile("balanced")).analyze(
            bars,
            symbol=normalized_symbol,
            timeframe=timeframe,
            asset_class=AssetClass.FOREX,
            source_dataset_id=dataset_id,
        )
        indicators = _indicators(bars)
        features = _feature_vectors(bars, normalized_symbol, structure, indicators)
        current = features[-1]
        feature_key = stable_id("fxfeatures", normalized_symbol, timeframe, bars[-1].close_time.isoformat(), len(bars))
        confluence = _confluence(current, structure)
        regime = _regime(current, indicators)
        explanation = _trade_explanation(current, structure, indicators, confluence, regime, instrument)
        structure_payload = structure.model_dump(mode="json")
        feature_vector_ids: list[str] = []
        if db is not None:
            feature_vector_ids = feature_store.put(
                db,
                rows=features,
                structure_payload=structure_payload,
                volatility_payload=indicators.model_dump(mode="json"),
                regime_payload=regime,
                confluence_payload=confluence,
                explanation_payload=explanation,
                source_provider=source_provider,
                source_dataset_id=dataset_id,
            )
        snapshot_id = stable_id("fxintel", normalized_symbol, timeframe, bars[-1].close_time.isoformat(), structure.configuration_hash)
        return ForexIntelligenceSnapshot(
            snapshot_id=snapshot_id,
            symbol=normalized_symbol,
            analysis_timestamp=datetime.now(timezone.utc),
            timeframe=timeframe,
            bars_analyzed=len(bars),
            market_structure=structure_payload,
            indicators=indicators,
            current_feature=current,
            feature_store_key=feature_key,
            feature_vector_id=feature_vector_ids[-1] if feature_vector_ids else None,
            feature_vector_ids=feature_vector_ids,
            feature_version=FEATURE_SCHEMA_VERSION,
            engine_version=INTELLIGENCE_ENGINE_VERSION,
            provider=source_provider,
            provider_symbol=provider_symbol_value or str(provider_symbol(normalized_symbol, "yahoo")),
            data_status="ok",
            freshness="latest",
            feature_count=len(features),
            confluence=confluence,
            regime=regime,
            trade_explanation=explanation,
            overlays=structure_payload.get("overlays", []),
            warnings=structure.warnings,
        )

    def latest(self, db: Session, *, symbol: str, timeframe: str = "1h") -> ForexFeatureVectorORM | None:
        return feature_store.latest(db, symbol=_normalize_symbol(symbol), timeframe=timeframe)

    def history(self, db: Session, *, symbol: str, timeframe: str = "1h", limit: int = 250, feature_version: str | None = None) -> list[ForexFeatureVectorORM]:
        return feature_store.history(db, symbol=_normalize_symbol(symbol), timeframe=timeframe, limit=limit, feature_version=feature_version)

    def get_feature_vector(self, db: Session, feature_vector_id: str) -> ForexFeatureVectorORM | None:
        return feature_store.get(db, feature_vector_id)


def _normalize_symbol(symbol: str) -> str:
    return normalize_forex_symbol(symbol)


def _indicators(bars: list[StructureBar]) -> ForexIndicatorSnapshot:
    closes = [float(bar.close) for bar in bars]
    atr_series = atr_values(bars)
    fast = ema(closes, 20)
    slow = ema(closes, 50)
    rsi_series = rsi_values(closes)
    macd_line, macd_signal = macd(closes)
    slope = regression_slope(closes)
    adx = adx_proxy(bars)
    atr = atr_series[-1]
    adr_window = [float(bar.high - bar.low) for bar in bars[-20:]]
    adr = mean(adr_window) if adr_window else None
    hv = historical_volatility(closes)
    trend_score = _bounded(
        (0.35 if fast[-1] is not None and slow[-1] is not None and fast[-1] > slow[-1] else -0.35)
        + ((adx or 0) / 100) * 0.35
        + (0.30 if (slope or 0) > 0 else -0.30 if (slope or 0) < 0 else 0),
        -1,
        1,
    )
    momentum_score = _bounded(
        ((rsi_series[-1] or 50) - 50) / 50 * 0.45
        + (0.30 if (macd_line or 0) > (macd_signal or 0) else -0.30)
        + _bounded((roc(closes) or 0) * 50, -0.25, 0.25),
        -1,
        1,
    )
    volatility_state = "unknown"
    if atr is not None and adr is not None:
        volatility_state = "expansion" if atr > adr * 1.15 else "compression" if atr < adr * 0.75 else "normal"
    return ForexIndicatorSnapshot(
        atr=atr,
        historical_volatility=hv,
        adr=adr,
        expected_daily_range=adr,
        volatility_state=volatility_state,
        ema_fast=fast[-1],
        ema_slow=slow[-1],
        adx=adx,
        regression_slope=slope,
        trend_score=trend_score,
        rsi=rsi_series[-1],
        stochastic_rsi=stochastic_rsi(rsi_series),
        macd=macd_line,
        macd_signal=macd_signal,
        roc=roc(closes),
        momentum_score=momentum_score,
    )


def _feature_vectors(bars: list[StructureBar], symbol: str, structure: Any, indicators: ForexIndicatorSnapshot) -> list[ForexFeatureVector]:
    out: list[ForexFeatureVector] = []
    for bar in bars:
        known_breaks = [item for item in structure.breaks if item.confirmation_time and item.confirmation_time <= bar.close_time]
        latest_break = known_breaks[-1] if known_breaks else None
        known_sweeps = [item for item in structure.liquidity_sweeps if item.confirmation_time and item.confirmation_time <= bar.close_time]
        known_fvgs = [zone for zone in structure.imbalances if zone.start_time <= bar.close_time and (not zone.invalidation_time or zone.invalidation_time > bar.close_time)]
        known_blocks = [block for block in structure.order_blocks if block.start_time <= bar.close_time and (not block.invalidation_time or block.invalidation_time > bar.close_time)]
        known_ranges = [rng for rng in structure.dealing_ranges if rng.start_time <= bar.close_time]
        latest_range = known_ranges[-1] if known_ranges else None
        position = latest_range.normalized_current_position if latest_range else None
        premium_discount = "unknown"
        if position is not None:
            premium_discount = "discount" if position < 0.45 else "premium" if position > 0.55 else "equilibrium"
        lookback = bars[max(0, bar.index - 20) : bar.index + 1]
        rolling_trend = "unknown"
        if len(lookback) >= 5:
            rolling_trend = "bullish" if float(lookback[-1].close) > float(lookback[0].close) else "bearish" if float(lookback[-1].close) < float(lookback[0].close) else "ranging"
        trend_score = indicators.trend_score if bar.index == bars[-1].index else _bounded((float(lookback[-1].close) - float(lookback[0].close)) * 500 if len(lookback) >= 2 else 0.0, -1, 1)
        momentum_score = indicators.momentum_score if bar.index == bars[-1].index else trend_score * 0.6
        evidence = [
            f"trend={rolling_trend}",
            f"liquidity_sweeps={len(known_sweeps)}",
            f"fvg={len(known_fvgs)}",
            f"order_blocks={len(known_blocks)}",
            f"premium_discount={premium_discount}",
            f"session={session_name(bar.close_time)}",
        ]
        score = _bounded(
            mean(
                [
                    abs(trend_score),
                    abs(momentum_score),
                    0.75 if known_breaks else 0.35,
                    1.0 if known_sweeps else 0.45 if structure.liquidity_levels else 0.0,
                    0.65 if known_fvgs or known_blocks else 0.0,
                    0.75,
                    0.75 if indicators.volatility_state in {"normal", "expansion"} else 0.35,
                    0.70 if position is not None and (position < 0.45 or position > 0.55) else 0.45 if position is not None else 0.0,
                ]
            ),
            0,
            1,
        )
        confidence = _bounded(score * (0.85 if bar.index + 1 >= 100 else 0.65), 0, 1)
        out.append(
            ForexFeatureVector(
                timestamp=bar.close_time,
                symbol=symbol,
                timeframe=bar.timeframe,
                close=float(bar.close),
                session=session_name(bar.close_time),
                session_range=session_range(bars[: bar.index + 1]),
                structure_trend=rolling_trend,
                latest_break=latest_break.break_kind if latest_break else None,
                latest_break_direction=latest_break.direction if latest_break else None,
                liquidity_sweeps=len(known_sweeps),
                active_fvgs=len(known_fvgs),
                active_order_blocks=len(known_blocks),
                premium_discount=premium_discount,
                dealing_range_position=position,
                trend_score=trend_score,
                momentum_score=momentum_score,
                volatility_state=indicators.volatility_state,
                confluence_score=score,
                confidence=confidence,
                evidence=evidence,
            )
        )
    return out


def _score_components(structure: Any, indicators: ForexIndicatorSnapshot, position: float | None) -> dict[str, float]:
    trend_component = abs(indicators.trend_score)
    momentum_component = abs(indicators.momentum_score)
    structure_component = 0.75 if structure.breaks else 0.35 if structure.swings else 0.0
    liquidity_component = 1.0 if structure.liquidity_sweeps else 0.45 if structure.liquidity_levels else 0.0
    zone_component = 0.65 if structure.imbalances or structure.order_blocks else 0.0
    session_component = 0.75
    volatility_component = 0.75 if indicators.volatility_state in {"normal", "expansion"} else 0.35
    premium_component = 0.70 if position is not None and (position < 0.45 or position > 0.55) else 0.45 if position is not None else 0.0
    return {
        "trend": trend_component,
        "momentum": momentum_component,
        "structure": structure_component,
        "liquidity": liquidity_component,
        "zones": zone_component,
        "session": session_component,
        "volatility": volatility_component,
        "premium_discount": premium_component,
    }


def _confluence(feature: ForexFeatureVector, structure: Any) -> dict[str, Any]:
    return {
        "score": feature.confluence_score,
        "confidence": feature.confidence,
        "direction_bias": "bullish" if feature.trend_score > 0 and feature.momentum_score > 0 else "bearish" if feature.trend_score < 0 and feature.momentum_score < 0 else "neutral",
        "components": _score_components(structure, ForexIndicatorSnapshot(trend_score=feature.trend_score, momentum_score=feature.momentum_score, volatility_state=feature.volatility_state), feature.dealing_range_position),
        "evidence": feature.evidence,
    }


def _regime(feature: ForexFeatureVector, indicators: ForexIndicatorSnapshot) -> dict[str, Any]:
    trend_abs = abs(indicators.trend_score)
    if indicators.volatility_state == "compression":
        label = "compression"
    elif indicators.volatility_state == "expansion" and trend_abs > 0.45:
        label = "breakout"
    elif trend_abs > 0.55:
        label = "trend"
    elif trend_abs < 0.20:
        label = "range"
    else:
        label = "mean_reverting"
    return {"label": label, "volatility": feature.volatility_state, "news_risk": "unknown", "read_only": True}


def _trade_explanation(
    feature: ForexFeatureVector,
    structure: Any,
    indicators: ForexIndicatorSnapshot,
    confluence: dict[str, Any],
    regime: dict[str, Any],
    instrument: ForexInstrument,
) -> dict[str, Any]:
    return {
        "market_summary": f"{instrument.display_name} {feature.timeframe} context is {regime['label']} with {confluence['direction_bias']} directional bias.",
        "trend": {"score": indicators.trend_score, "state": feature.structure_trend},
        "structure": {"latest_break": feature.latest_break, "direction": feature.latest_break_direction, "swings": len(structure.swings)},
        "liquidity": {"sweeps": feature.liquidity_sweeps, "levels": len(structure.liquidity_levels)},
        "session": {"name": feature.session, "range": feature.session_range},
        "volatility": {"state": indicators.volatility_state, "atr": indicators.atr, "historical_volatility": indicators.historical_volatility},
        "confluence": confluence,
        "risk": {"risk_engine_required": True, "engine_submits_trades": False},
        "expected_probability": round(0.5 + (feature.confluence_score - 0.5) * 0.35, 4),
        "invalidation": "Use latest opposite swing, liquidity sweep failure, or risk-engine stop model before paper trading.",
        "reasoning": feature.evidence,
        "evidence": feature.evidence,
        "confidence": feature.confidence,
        "missing_evidence": ["scheduled high-impact news"] if regime["news_risk"] == "unknown" else [],
    }


def _bounded(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
