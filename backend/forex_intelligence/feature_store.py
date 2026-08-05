from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.forex_intelligence.models import ForexFeatureVector
from backend.forex_intelligence.orm import ForexFeatureVectorORM
from backend.market_structure.models import stable_id

FEATURE_SCHEMA_VERSION = "fx-feature-v2"
INTELLIGENCE_ENGINE_VERSION = "fx-intelligence-v2"


def content_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ForexFeatureStore:
    def put(
        self,
        db: Session,
        *,
        rows: list[ForexFeatureVector],
        structure_payload: dict[str, Any],
        volatility_payload: dict[str, Any],
        regime_payload: dict[str, Any],
        confluence_payload: dict[str, Any],
        explanation_payload: dict[str, Any],
        source_provider: str,
        source_dataset_id: str,
    ) -> list[str]:
        ids: list[str] = []
        generated_at = datetime.now(timezone.utc)
        liquidity_payload = {
            "liquidity_levels": structure_payload.get("liquidity_levels", []),
            "liquidity_sweeps": structure_payload.get("liquidity_sweeps", []),
        }
        trend_payload = {"trend": structure_payload.get("trend"), "breaks": structure_payload.get("breaks", [])}
        momentum_payload = {"engine_version": INTELLIGENCE_ENGINE_VERSION}
        session_payload = {"sessions": structure_payload.get("session_levels", [])}

        for row in rows:
            feature_payload = row.model_dump(mode="json")
            candle_payload = {"symbol": row.symbol, "timeframe": row.timeframe, "timestamp": row.timestamp.isoformat(), "close": row.close}
            candle_hash = content_hash(candle_payload)
            feature_vector_id = stable_id("fxfv", row.symbol, row.timeframe, row.timestamp.isoformat(), FEATURE_SCHEMA_VERSION, source_dataset_id)
            ids.append(feature_vector_id)
            existing = db.get(ForexFeatureVectorORM, feature_vector_id)
            if existing is None:
                existing = ForexFeatureVectorORM(feature_vector_id=feature_vector_id)
                db.add(existing)
            existing.symbol = row.symbol
            existing.timeframe = row.timeframe
            existing.candle_timestamp = row.timestamp
            existing.candle_content_hash = candle_hash
            existing.feature_version = FEATURE_SCHEMA_VERSION
            existing.engine_version = INTELLIGENCE_ENGINE_VERSION
            existing.source_provider = source_provider
            existing.source_dataset_id = source_dataset_id
            existing.source_candle_id = stable_id("fxcandle", row.symbol, row.timeframe, row.timestamp.isoformat(), candle_hash)
            existing.generated_at = generated_at
            existing.completeness = "complete"
            existing.quality_status = "ok"
            existing.feature_payload = feature_payload
            existing.structure_payload = structure_payload
            existing.liquidity_payload = liquidity_payload
            existing.volatility_payload = volatility_payload
            existing.trend_payload = trend_payload
            existing.momentum_payload = momentum_payload
            existing.session_payload = session_payload
            existing.regime_payload = regime_payload
            existing.confluence_payload = confluence_payload
            existing.explanation_payload = explanation_payload
        db.commit()
        return ids

    def latest(self, db: Session, *, symbol: str, timeframe: str) -> ForexFeatureVectorORM | None:
        return db.execute(
            select(ForexFeatureVectorORM)
            .where(ForexFeatureVectorORM.symbol == symbol, ForexFeatureVectorORM.timeframe == timeframe)
            .order_by(ForexFeatureVectorORM.candle_timestamp.desc())
            .limit(1)
        ).scalar_one_or_none()

    def history(
        self,
        db: Session,
        *,
        symbol: str,
        timeframe: str,
        limit: int = 250,
        feature_version: str | None = None,
    ) -> list[ForexFeatureVectorORM]:
        stmt = select(ForexFeatureVectorORM).where(ForexFeatureVectorORM.symbol == symbol, ForexFeatureVectorORM.timeframe == timeframe)
        if feature_version:
            stmt = stmt.where(ForexFeatureVectorORM.feature_version == feature_version)
        return list(db.execute(stmt.order_by(ForexFeatureVectorORM.candle_timestamp.desc()).limit(limit)).scalars())

    def get(self, db: Session, feature_vector_id: str) -> ForexFeatureVectorORM | None:
        return db.get(ForexFeatureVectorORM, feature_vector_id)


feature_store = ForexFeatureStore()
