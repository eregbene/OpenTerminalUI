from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Index, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.shared.db import Base


class ForexFrameworkSignalORM(Base):
    __tablename__ = "forex_framework_signals"

    framework_signal_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    framework_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    framework_version: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    analysis_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    feature_vector_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    source_dataset_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    bias: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    signal_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    quality: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    signal_payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    __table_args__ = (
        UniqueConstraint("framework_id", "framework_version", "symbol", "timeframe", "analysis_timestamp", "feature_vector_id", name="uq_forex_framework_signal_version"),
        Index("ix_forex_framework_signal_symbol_tf", "symbol", "timeframe", "analysis_timestamp"),
    )
