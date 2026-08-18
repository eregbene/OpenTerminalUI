"""Strategy performance monitor persistence (2026-08-18).

One table: strategy_performance_recommendations. Each row is a RECOMMENDATION, never an action --
see performance_monitor.py's own module docstring for why this deliberately never auto-applies an
activation change. Deterministic per (strategy_id, day) so repeated runs on the same day upsert
the same row rather than accumulating duplicates, giving a natural daily history over time.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String

from backend.shared.db import Base


class StrategyPerformanceRecommendationORM(Base):
    __tablename__ = "strategy_performance_recommendations"

    recommendation_id = Column(String(160), primary_key=True)
    strategy_id = Column(String(64), nullable=False)
    computed_at = Column(DateTime(timezone=True), nullable=False)
    window_days = Column(Integer, nullable=False)
    data_source = Column(String(24), nullable=False)  # REAL_TRADES | SHADOW_TRACKING
    sample_size = Column(Integer, nullable=False)
    win_rate = Column(Float, nullable=True)
    expectancy_r = Column(Float, nullable=True)
    realized_usd = Column(Float, nullable=True)
    avg_realized_usd = Column(Float, nullable=True)
    current_activation = Column(String(24), nullable=False)
    recommended_activation = Column(String(24), nullable=False)
    reasoning = Column(String(2000), nullable=False)
    status = Column(String(24), nullable=False, default="PENDING_REVIEW")  # PENDING_REVIEW | APPROVED | REJECTED | APPLIED | NO_ACTION_NEEDED
    reviewed_by = Column(String(120), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    applied_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
