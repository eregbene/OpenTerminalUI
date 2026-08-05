from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.forex_frameworks.models import FrameworkBias, FrameworkComparison, FrameworkContext, FrameworkSignal, FrameworkThesis, PriceZone
from backend.forex_frameworks.orm import ForexFrameworkSignalORM
from backend.forex_frameworks.registry import registry
from backend.forex_intelligence.instruments import get_forex_instrument, normalize_forex_symbol
from backend.market_structure.models import stable_id


def _hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def context_from_snapshot(snapshot: dict[str, Any], candles: list[dict[str, Any]]) -> FrameworkContext:
    symbol = normalize_forex_symbol(snapshot["symbol"])
    instrument = get_forex_instrument(symbol)
    feature = snapshot.get("current_feature") or {}
    return FrameworkContext(
        symbol=symbol,
        asset_type=instrument.asset_type,
        instrument_class=instrument.instrument_class,
        timeframe=snapshot["timeframe"],
        analysis_timestamp=datetime.fromisoformat(str(snapshot["analysis_timestamp"]).replace("Z", "+00:00")),
        completed_candles=list(candles),
        feature_vectors=[feature],
        current_feature=feature,
        market_structure=snapshot.get("market_structure") or {},
        indicators=snapshot.get("indicators") or {},
        liquidity={
            "liquidity_levels": (snapshot.get("market_structure") or {}).get("liquidity_levels", []),
            "liquidity_sweeps": (snapshot.get("market_structure") or {}).get("liquidity_sweeps", []),
        },
        session={"name": feature.get("session"), "range": feature.get("session_range")},
        volatility={"state": feature.get("volatility_state"), "atr": (snapshot.get("indicators") or {}).get("atr")},
        trend={"score": feature.get("trend_score"), "structure_trend": feature.get("structure_trend")},
        momentum={"score": feature.get("momentum_score")},
        regime=snapshot.get("regime") or {},
        provider_metadata={
            "provider": snapshot.get("provider"),
            "provider_symbol": snapshot.get("provider_symbol"),
            "data_source_type": instrument.data_source_type,
            "is_proxy": instrument.is_proxy,
            "proxy_for": instrument.proxy_for,
        },
        data_quality_status=snapshot.get("data_status") or "ok",
        feature_version=snapshot.get("feature_version") or "unknown",
        feature_vector_id=snapshot.get("feature_vector_id"),
        source_dataset_id=snapshot.get("feature_store_key"),
    )


class ForexFrameworkService:
    def analyze(self, ctx: FrameworkContext, framework_ids: list[str] | None = None) -> list[FrameworkSignal]:
        selected = [registry.require(framework_id) for framework_id in framework_ids] if framework_ids else registry.all(enabled_only=True)
        return [framework.analyze(ctx) for framework in selected]

    def persist(self, db: Session, ctx: FrameworkContext, signals: list[FrameworkSignal]) -> list[str]:
        ids: list[str] = []
        for signal in signals:
            payload = signal.model_dump(mode="json")
            content_hash = _hash(payload)
            signal_id = stable_id("fxfw", signal.framework_id, signal.framework_version, signal.symbol, signal.timeframe, signal.analysis_timestamp.isoformat(), ctx.feature_vector_id or "no-feature", content_hash)
            ids.append(signal_id)
            existing = db.get(ForexFrameworkSignalORM, signal_id)
            if existing is None:
                existing = ForexFrameworkSignalORM(framework_signal_id=signal_id)
                db.add(existing)
            existing.framework_id = signal.framework_id
            existing.framework_version = signal.framework_version
            existing.symbol = signal.symbol
            existing.timeframe = signal.timeframe
            existing.analysis_timestamp = signal.analysis_timestamp
            existing.feature_vector_id = ctx.feature_vector_id
            existing.source_dataset_id = ctx.source_dataset_id
            existing.bias = signal.bias.value
            existing.signal_type = signal.signal_type
            existing.confidence = signal.confidence
            existing.quality = signal.quality
            existing.status = signal.status.value
            existing.content_hash = content_hash
            existing.generated_at = signal.generated_at
            existing.signal_payload = payload
        db.commit()
        return ids

    def latest(self, db: Session, *, symbol: str, timeframe: str = "1h", framework_id: str | None = None) -> list[dict[str, Any]]:
        stmt = select(ForexFrameworkSignalORM).where(ForexFrameworkSignalORM.symbol == normalize_forex_symbol(symbol), ForexFrameworkSignalORM.timeframe == timeframe)
        if framework_id:
            stmt = stmt.where(ForexFrameworkSignalORM.framework_id == framework_id)
        rows = list(db.execute(stmt.order_by(ForexFrameworkSignalORM.analysis_timestamp.desc()).limit(200)).scalars())
        if framework_id:
            return [rows[0].signal_payload] if rows else []
        latest_ts = rows[0].analysis_timestamp if rows else None
        return [row.signal_payload for row in rows if row.analysis_timestamp == latest_ts]

    def history(self, db: Session, *, symbol: str, timeframe: str = "1h", framework_id: str | None = None, limit: int = 250) -> list[dict[str, Any]]:
        stmt = select(ForexFrameworkSignalORM).where(ForexFrameworkSignalORM.symbol == normalize_forex_symbol(symbol), ForexFrameworkSignalORM.timeframe == timeframe)
        if framework_id:
            stmt = stmt.where(ForexFrameworkSignalORM.framework_id == framework_id)
        rows = list(db.execute(stmt.order_by(ForexFrameworkSignalORM.analysis_timestamp.desc()).limit(limit)).scalars())
        return [row.signal_payload for row in rows]

    def compare(self, signals: list[FrameworkSignal]) -> FrameworkComparison:
        valid = [signal for signal in signals if signal.status.value in {"VALID", "PARTIAL"}]
        bullish = [signal for signal in valid if "BULLISH" in signal.bias.value]
        bearish = [signal for signal in valid if "BEARISH" in signal.bias.value]
        neutral = [signal for signal in valid if signal.bias == FrameworkBias.NEUTRAL]
        unknown = [signal for signal in signals if signal.bias == FrameworkBias.UNKNOWN]
        weighted_bull = sum(signal.confidence * registry.require(signal.framework_id).weight for signal in bullish)
        weighted_bear = sum(signal.confidence * registry.require(signal.framework_id).weight for signal in bearish)
        denominator = max(weighted_bull + weighted_bear + sum(signal.confidence for signal in neutral), 1e-9)
        conflict_ratio = min(weighted_bull, weighted_bear) / max(weighted_bull, weighted_bear, 1e-9) if bullish and bearish else 0.0
        agreement_ratio = max(weighted_bull, weighted_bear, sum(signal.confidence for signal in neutral)) / denominator
        bias = FrameworkBias.NEUTRAL
        if weighted_bull > weighted_bear * 1.25 and weighted_bull > 0:
            bias = FrameworkBias.BULLISH
        elif weighted_bear > weighted_bull * 1.25 and weighted_bear > 0:
            bias = FrameworkBias.BEARISH
        conflicts = []
        if bullish and bearish:
            conflicts.append(f"{len(bullish)} bullish frameworks conflict with {len(bearish)} bearish frameworks")
        overlapping = ["SMC, ICT, market_structure share objective structure/liquidity evidence"]
        quality = sum(signal.quality for signal in signals) / max(len(signals), 1)
        first = signals[0]
        return FrameworkComparison(
            symbol=first.symbol,
            timeframe=first.timeframe,
            analysis_timestamp=first.analysis_timestamp,
            bullish_framework_count=len(bullish),
            bearish_framework_count=len(bearish),
            neutral_framework_count=len(neutral),
            unknown_framework_count=len(unknown),
            weighted_bullish_score=round(weighted_bull, 4),
            weighted_bearish_score=round(weighted_bear, 4),
            agreement_ratio=round(agreement_ratio, 4),
            conflict_ratio=round(conflict_ratio, 4),
            data_quality_score=round(quality, 4),
            overall_framework_bias=bias,
            overlapping_evidence=overlapping,
            conflicts=conflicts,
            signals=signals,
        )

    def thesis(self, comparison: FrameworkComparison) -> FrameworkThesis:
        signals = comparison.signals
        supporting = [item for signal in signals for item in signal.supporting_evidence[:2]][:12]
        conflicting = comparison.conflicts + [item for signal in signals for item in signal.conflicting_evidence[:1]][:8]
        missing = [item for signal in signals for item in signal.missing_evidence[:1]][:10]
        entry = next((signal.entry_zone for signal in signals if signal.entry_zone), None)
        invalidation = next((signal.invalidation_zone for signal in signals if signal.invalidation_zone), None)
        targets = next((signal.target_zones for signal in signals if signal.target_zones), [])
        confidence = min(0.85, max(comparison.weighted_bullish_score, comparison.weighted_bearish_score) / max(len(signals), 1))
        if comparison.conflict_ratio > 0.25:
            confidence *= 0.7
        return FrameworkThesis(
            symbol=comparison.symbol,
            timeframe=comparison.timeframe,
            directional_bias=comparison.overall_framework_bias,
            confidence=round(confidence, 4),
            market_regime=signals[0].market_regime if signals else "unknown",
            framework_agreement=comparison.model_dump(mode="json", exclude={"signals"}),
            framework_disagreement=comparison.conflicts,
            candidate_entry_zone=entry,
            invalidation=invalidation,
            candidate_target_zones=targets,
            risk_reward_estimate=None,
            supporting_evidence=supporting,
            conflicting_evidence=conflicting,
            missing_evidence=missing,
            data_freshness="latest",
            framework_versions={signal.framework_id: signal.framework_version for signal in signals},
        )


framework_service = ForexFrameworkService()
