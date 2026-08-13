"""Edge-quality investigation Phase C regression tests: strategy x symbol x regime segmentation.

Covers: the STRONG/ACCEPTABLE/DEGRADED/FAILED_OOS/INSUFFICIENT_SAMPLE -> POSITIVE_OOS/PROMISING/
NEUTRAL/NEGATIVE_OOS/INSUFFICIENT_SAMPLE classification mapping, real-combination enumeration
(only combinations that actually occur are evaluated), and combination() answering the roadmap's
own worked example on synthetic fixtures with a known-clean edge vs a known-negative edge."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.historical_intelligence import segment_matrix, walk_forward
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM
from backend.shared.db import Base

NOW = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(walk_forward, "SessionLocal", SessionLocal)
    import backend.shared.db as shared_db

    monkeypatch.setattr(shared_db, "SessionLocal", SessionLocal)
    return SessionLocal


def _seed(db, *, n: int, strategy: str, symbol: str, regime: str, start: datetime, spacing: timedelta, r_values: list[float]):
    for i in range(n):
        entry_time = start + spacing * i
        db.add(HistoricalPatternFingerprintORM(
            fingerprint_id=f"FP_{strategy}_{symbol}_{regime}_{i}", historical_intelligence_version="hi-v1", strategy_version="replay-v1", fingerprint_version="fp-v2",
            source_quality_tier="RECONSTRUCTED", provider="MT5", proxy=False, canonical_symbol=symbol, direction="LONG",
            anchor_strategy=strategy, contributing_strategies=[strategy], liquidity_location="none", fvg_state="none", order_block_state="none",
            spread_regime="unknown", regime=regime, entry=1.1, stop_loss=1.09, take_profit=1.12, entry_time=entry_time, peer_group_hash="H", created_at=entry_time,
        ))
        db.add(HistoricalSetupOutcomeORM(outcome_id=f"OUT_{strategy}_{symbol}_{regime}_{i}", fingerprint_id=f"FP_{strategy}_{symbol}_{regime}_{i}", resolution_status="RESOLVED", outcome_r=r_values[i], data_quality="HIGH", bars_scanned=5, created_at=entry_time))


# --- classification mapping (pure) ----------------------------------------------------------


def test_classification_map_covers_every_edge_stability_value():
    for value in (walk_forward.EDGE_STRONG, walk_forward.EDGE_ACCEPTABLE, walk_forward.EDGE_DEGRADED, walk_forward.EDGE_FAILED_OOS, walk_forward.EDGE_INSUFFICIENT_SAMPLE):
        result = segment_matrix.classify_combination({"edge_stability": value})
        assert result in {segment_matrix.POSITIVE_OOS, segment_matrix.PROMISING, segment_matrix.NEUTRAL, segment_matrix.NEGATIVE_OOS, segment_matrix.INSUFFICIENT_SAMPLE}


def test_strong_maps_to_positive_oos():
    assert segment_matrix.classify_combination({"edge_stability": walk_forward.EDGE_STRONG}) == segment_matrix.POSITIVE_OOS


def test_failed_oos_maps_to_negative_oos():
    assert segment_matrix.classify_combination({"edge_stability": walk_forward.EDGE_FAILED_OOS}) == segment_matrix.NEGATIVE_OOS


# --- combination(): the roadmap's own worked example -----------------------------------------


def test_combination_reports_negative_oos_for_a_consistently_losing_combo(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed(db, n=60, strategy="mtfai1", symbol="NZDUSD", regime="breakout", start=NOW, spacing=timedelta(hours=1), r_values=[-1.0] * 60)
        db.commit()

    result = segment_matrix.combination(anchor_strategy="mtfai1", canonical_symbol="NZDUSD", regime="breakout")
    assert result["classification"] == segment_matrix.NEGATIVE_OOS
    assert result["sample_size"] == 60


def test_combination_reports_positive_oos_for_a_consistently_winning_combo(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed(db, n=60, strategy="mtfai1", symbol="EURUSD", regime="trending_up", start=NOW, spacing=timedelta(hours=1), r_values=[1.0] * 60)
        db.commit()

    result = segment_matrix.combination(anchor_strategy="mtfai1", canonical_symbol="EURUSD", regime="trending_up")
    assert result["classification"] == segment_matrix.POSITIVE_OOS


def test_combination_insufficient_sample_below_floor(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed(db, n=5, strategy="mtfai1", symbol="GBPUSD", regime="ranging", start=NOW, spacing=timedelta(hours=1), r_values=[1.0] * 5)
        db.commit()

    result = segment_matrix.combination(anchor_strategy="mtfai1", canonical_symbol="GBPUSD", regime="ranging")
    assert result["classification"] == segment_matrix.INSUFFICIENT_SAMPLE


# --- real-combination enumeration -------------------------------------------------------------


def test_strategy_symbol_matrix_only_reports_real_combinations(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed(db, n=10, strategy="mtfai1", symbol="EURUSD", regime="trending_up", start=NOW, spacing=timedelta(hours=1), r_values=[1.0] * 10)
        db.commit()

    results = segment_matrix.strategy_symbol_matrix(min_sample=5)
    assert len(results) == 1  # not a cross-product of every strategy x every symbol
    assert results[0]["anchor_strategy"] == "mtfai1"
    assert results[0]["canonical_symbol"] == "EURUSD"


def test_strategy_symbol_regime_matrix_isolates_different_regimes_for_the_same_pair(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed(db, n=60, strategy="mtfai1", symbol="EURUSD", regime="trending_up", start=NOW, spacing=timedelta(hours=1), r_values=[1.0] * 60)
        _seed(db, n=60, strategy="mtfai1", symbol="EURUSD", regime="ranging", start=NOW + timedelta(days=10), spacing=timedelta(hours=1), r_values=[-1.0] * 60)
        db.commit()

    results = segment_matrix.strategy_symbol_regime_matrix(min_sample=5)
    by_regime = {r["regime"]: r["classification"] for r in results}
    assert by_regime["trending_up"] != by_regime["ranging"]  # same strategy+symbol, different regime, different verdict
