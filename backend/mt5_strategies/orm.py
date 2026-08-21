"""Strategy performance monitor persistence (2026-08-18).

One table: strategy_performance_recommendations. Each row is a RECOMMENDATION, never an action --
see performance_monitor.py's own module docstring for why this deliberately never auto-applies an
activation change. Deterministic per (strategy_id, day) so repeated runs on the same day upsert
the same row rather than accumulating duplicates, giving a natural daily history over time.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, Integer, String

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


class StrategyLifecycleStateORM(Base):
    """Priority 6 (2026-08-21): one row per strategy, the CURRENT lifecycle state -- see
    lifecycle.py's module docstring for the full state model and why this is an additive layer
    on top of (never a replacement for) MT5_STRATEGY_ACTIVATION_<ID>."""

    __tablename__ = "strategy_lifecycle_states"

    strategy_id = Column(String(64), primary_key=True)
    lifecycle_state = Column(String(24), nullable=False)
    mapped_activation = Column(String(24), nullable=False)  # ACTIVE_MT5 | SHADOW_MT5 | DISABLED -- what this state WOULD map to; not itself an env-var write
    strategy_version_fingerprint = Column(String(64), nullable=True)
    evidence_version_fingerprint = Column(String(64), nullable=True)
    version_status = Column(String(24), nullable=False, default="CONSISTENT")  # CONSISTENT | REQUIRES_CLASSIFICATION | REFACTOR_ONLY | BEHAVIORAL_CHANGE_PENDING_REVALIDATION
    pending_recommendation = Column(String(24), nullable=True)  # PROMOTE_RECOMMENDATION | DEMOTE_RECOMMENDATION | None
    pending_recommendation_reason = Column(String(2000), nullable=True)
    pending_recommendation_evidence = Column(JSON, nullable=False, default=dict)
    consecutive_negative_periods = Column(Integer, nullable=False, default=0)
    consecutive_positive_periods = Column(Integer, nullable=False, default=0)
    degraded_since = Column(DateTime(timezone=True), nullable=True)
    last_transition_at = Column(DateTime(timezone=True), nullable=True)
    last_transition_reason = Column(String(2000), nullable=True)
    last_evaluated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class StrategyLifecycleEventORM(Base):
    """Priority 6: append-only audit history -- every transition, recommendation, human
    decision, and version-invalidation event, so any state is always explainable (Part 13's
    "no opaque status changes")."""

    __tablename__ = "strategy_lifecycle_events"

    event_id = Column(String(160), primary_key=True)
    strategy_id = Column(String(64), nullable=False)
    event_type = Column(String(32), nullable=False)  # TRANSITION | PROMOTE_RECOMMENDATION | DEMOTE_RECOMMENDATION | HUMAN_APPROVED | HUMAN_REJECTED | VERSION_FLAGGED | VERSION_CLASSIFIED | EMERGENCY_SUSPENDED
    from_state = Column(String(24), nullable=True)
    to_state = Column(String(24), nullable=True)
    reason = Column(String(2000), nullable=False)
    evidence = Column(JSON, nullable=False, default=dict)
    strategy_version_fingerprint = Column(String(64), nullable=True)
    approved_by = Column(String(120), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
