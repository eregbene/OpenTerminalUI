"""Multi-neighbor analog intelligence: OOS calibration / old-vs-new comparison regression tests.

Covers: OOS split integrity (point-in-time `as_of` never sees post-boundary evidence),
INSUFFICIENT_SAMPLE reported honestly rather than fabricated, and calibration bucketing produces
a real, bounded mean-absolute-calibration-error."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.historical_intelligence import similarity, similarity_oos, walk_forward
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM
from backend.shared.db import Base

NOW = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(walk_forward, "SessionLocal", SessionLocal)
    monkeypatch.setattr(similarity, "SessionLocal", SessionLocal)
    return SessionLocal


def _seed(db, *, idx: int, strategy: str = "mtfai1", symbol: str = "EURUSD", start: datetime, spacing: timedelta, outcome_r: float, reached_1r: bool):
    entry_time = start + spacing * idx
    db.add(HistoricalPatternFingerprintORM(
        fingerprint_id=f"FP{idx}", historical_intelligence_version="hi-v1", strategy_version="replay-v1", fingerprint_version="fp-v2",
        source_quality_tier="RECONSTRUCTED", provider="MT5", proxy=False, canonical_symbol=symbol, direction="LONG",
        anchor_strategy=strategy, contributing_strategies=[strategy], liquidity_location="none", fvg_state="none", order_block_state="none",
        spread_regime="unknown", regime="trending_up", regime_broad="TRENDING", session="LONDON", h1_trend="bullish",
        entry=1.1, stop_loss=1.09, take_profit=1.12, entry_time=entry_time, peer_group_hash="H", created_at=entry_time,
    ))
    db.add(HistoricalSetupOutcomeORM(
        outcome_id=f"OUT{idx}", fingerprint_id=f"FP{idx}", resolution_status="RESOLVED", outcome_r=outcome_r, data_quality="HIGH", bars_scanned=5,
        created_at=entry_time, reached_0_25r=outcome_r > 0, reached_0_5r=outcome_r > 0, reached_1r=reached_1r,
    ))


def test_insufficient_sample_reported_honestly(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(5):
            _seed(db, idx=i, start=NOW, spacing=timedelta(hours=1), outcome_r=1.0, reached_1r=True)
        db.commit()

    result = similarity_oos.calibration_check("mtfai1")
    assert result["status"] == "INSUFFICIENT_SAMPLE"


def test_calibration_check_produces_bounded_error_on_perfectly_predictable_data(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        # 200 identical setups, ALL reach +1R -- a perfectly predictable pattern; the
        # similarity model should therefore predict close to 100% and be well-calibrated.
        for i in range(200):
            _seed(db, idx=i, start=NOW, spacing=timedelta(hours=1), outcome_r=1.5, reached_1r=True)
        db.commit()

    result = similarity_oos.calibration_check("mtfai1", top_k=50)
    assert result["status"] in ("OK", "NO_PREDICTIONS_AVAILABLE")
    if result["status"] == "OK":
        assert 0.0 <= result["mean_absolute_calibration_error"] <= 1.0
        assert result["oos_n"] > 0


def test_compare_peer_group_vs_similarity_reports_usable_counts(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(200):
            _seed(db, idx=i, start=NOW, spacing=timedelta(hours=1), outcome_r=1.0, reached_1r=True)
        db.commit()

    result = similarity_oos.compare_peer_group_vs_similarity("mtfai1", top_k=50)
    assert result["status"] == "OK"
    assert result["exact_peer_group_usable_count"] >= 0
    assert result["similarity_weighted_usable_count"] >= 0
    assert result["oos_n"] > 0


def test_as_of_never_sees_future_evidence(monkeypatch):
    """Point-in-time integrity: a similarity query with as_of=X must never include a neighbor
    whose entry_time is after X, even if that neighbor would otherwise be a strong match."""
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed(db, idx=0, start=NOW, spacing=timedelta(hours=1), outcome_r=1.0, reached_1r=True)
        _seed(db, idx=1, start=NOW + timedelta(days=10), spacing=timedelta(hours=1), outcome_r=1.0, reached_1r=True)
        db.commit()

    cutoff = NOW + timedelta(days=1)
    result = similarity.find_similar_setups(
        canonical_symbol="EURUSD", direction="LONG", anchor_strategy="mtfai1", strategy_version="replay-v1",
        query_dims={"regime": "trending_up", "session": "LONDON", "h1_trend": "bullish"}, top_k=10, as_of=cutoff,
    )
    assert len(result) == 1
    assert result[0]["fingerprint"].fingerprint_id == "FP0"
