from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.shared.db import Base


class ForexFeatureVectorORM(Base):
    __tablename__ = "forex_feature_vectors"

    feature_vector_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    candle_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    candle_content_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    feature_version: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_dataset_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    source_candle_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    completeness: Mapped[str] = mapped_column(String(32), nullable=False, default="complete", index=True)
    quality_status: Mapped[str] = mapped_column(String(32), nullable=False, default="ok", index=True)
    feature_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    structure_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    liquidity_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    volatility_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    trend_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    momentum_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    session_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    regime_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    confluence_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    explanation_payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "candle_timestamp", "feature_version", "source_dataset_id", name="uq_forex_feature_vector_version"),
        Index("ix_forex_features_symbol_tf_time", "symbol", "timeframe", "candle_timestamp"),
    )
