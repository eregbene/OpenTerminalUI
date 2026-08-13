"""Phase 6 (Forex/MT5 roadmap) regression tests: strategy/symbol/regime return correlation
analytics. Covers: UNTRUSTED/unresolved outcomes excluded from the return series, daily
averaging within a (group, day) cell, correlation only reported once MIN_OVERLAP_DAYS is met,
a constant series never producing a fabricated correlation, and perfectly co-moving vs
perfectly inverse series producing the expected +1/-1."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.historical_intelligence import correlation
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM
from backend.shared.db import Base

NOW = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(correlation, "SessionLocal", SessionLocal, raising=False)
    import backend.shared.db as shared_db

    monkeypatch.setattr(shared_db, "SessionLocal", SessionLocal)
    return SessionLocal


def _seed_outcome(db, *, idx: int, strategy: str, symbol: str, day_offset: int, outcome_r: float, resolution_status: str = "RESOLVED", data_quality: str = "HIGH"):
    entry_time = NOW + timedelta(days=day_offset)
    db.add(HistoricalPatternFingerprintORM(
        fingerprint_id=f"FP{idx}", historical_intelligence_version="hi-v1", strategy_version="replay-v1", fingerprint_version="fp-v2",
        source_quality_tier="RECONSTRUCTED", provider="MT5", proxy=False, canonical_symbol=symbol, direction="LONG",
        anchor_strategy=strategy, contributing_strategies=[strategy], liquidity_location="none", fvg_state="none", order_block_state="none",
        spread_regime="unknown", entry=1.1, stop_loss=1.09, take_profit=1.12, entry_time=entry_time, peer_group_hash="H", created_at=entry_time,
    ))
    db.add(HistoricalSetupOutcomeORM(outcome_id=f"OUT{idx}", fingerprint_id=f"FP{idx}", resolution_status=resolution_status, outcome_r=outcome_r, data_quality=data_quality, bars_scanned=5, created_at=entry_time))


def test_daily_return_series_excludes_untrusted_and_unresolved(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed_outcome(db, idx=1, strategy="mtfai1", symbol="EURUSD", day_offset=0, outcome_r=1.0)
        _seed_outcome(db, idx=2, strategy="mtfai1", symbol="EURUSD", day_offset=1, outcome_r=999.0, data_quality="UNTRUSTED")
        _seed_outcome(db, idx=3, strategy="mtfai1", symbol="EURUSD", day_offset=2, outcome_r=999.0, resolution_status="PENDING")
        db.commit()

    series = correlation.daily_return_series(correlation.GROUP_STRATEGY)
    assert list(series["mtfai1"].values()) == [1.0]


def test_daily_return_series_averages_within_same_day(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed_outcome(db, idx=1, strategy="momentum", symbol="EURUSD", day_offset=0, outcome_r=1.0)
        _seed_outcome(db, idx=2, strategy="momentum", symbol="EURUSD", day_offset=0, outcome_r=3.0)
        db.commit()

    series = correlation.daily_return_series(correlation.GROUP_STRATEGY)
    values = list(series["momentum"].values())
    assert values == [2.0]  # mean of 1.0 and 3.0


def test_correlation_requires_min_overlap_days(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(3):
            _seed_outcome(db, idx=i, strategy="a", symbol="EURUSD", day_offset=i, outcome_r=1.0)
        for i in range(3, 6):
            _seed_outcome(db, idx=i, strategy="b", symbol="GBPUSD", day_offset=i - 3, outcome_r=1.0)
        db.commit()

    result = correlation.correlation_matrix(correlation.GROUP_STRATEGY)
    pair = next(p for p in result["pairs"] if {p["a"], p["b"]} == {"a", "b"})
    assert pair["correlation"] is None  # only 3 overlapping days, below MIN_OVERLAP_DAYS=10
    assert pair["overlap_days"] == 3


def test_correlation_perfectly_comoving_series_is_positive_one(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        idx = 0
        for day in range(15):
            r = 1.0 if day % 2 == 0 else -1.0
            _seed_outcome(db, idx=idx, strategy="a", symbol="EURUSD", day_offset=day, outcome_r=r)
            idx += 1
            _seed_outcome(db, idx=idx, strategy="b", symbol="GBPUSD", day_offset=day, outcome_r=r)  # identical pattern
            idx += 1
        db.commit()

    result = correlation.correlation_matrix(correlation.GROUP_STRATEGY)
    pair = next(p for p in result["pairs"] if {p["a"], p["b"]} == {"a", "b"})
    assert pair["correlation"] == 1.0
    assert pair["high_correlation"] is True


def test_correlation_perfectly_inverse_series_is_negative_one(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        idx = 0
        for day in range(15):
            r = 1.0 if day % 2 == 0 else -1.0
            _seed_outcome(db, idx=idx, strategy="a", symbol="EURUSD", day_offset=day, outcome_r=r)
            idx += 1
            _seed_outcome(db, idx=idx, strategy="b", symbol="GBPUSD", day_offset=day, outcome_r=-r)  # inverted
            idx += 1
        db.commit()

    result = correlation.correlation_matrix(correlation.GROUP_STRATEGY)
    pair = next(p for p in result["pairs"] if {p["a"], p["b"]} == {"a", "b"})
    assert pair["correlation"] == -1.0


def test_correlation_constant_series_never_fabricates_a_value(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        idx = 0
        for day in range(15):
            _seed_outcome(db, idx=idx, strategy="a", symbol="EURUSD", day_offset=day, outcome_r=0.5)  # constant
            idx += 1
            _seed_outcome(db, idx=idx, strategy="b", symbol="GBPUSD", day_offset=day, outcome_r=float(day))
            idx += 1
        db.commit()

    result = correlation.correlation_matrix(correlation.GROUP_STRATEGY)
    pair = next(p for p in result["pairs"] if {p["a"], p["b"]} == {"a", "b"})
    assert pair["correlation"] is None


def test_full_report_covers_all_three_dimensions(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed_outcome(db, idx=1, strategy="mtfai1", symbol="EURUSD", day_offset=0, outcome_r=1.0)
        db.commit()

    report = correlation.full_report()
    assert set(report.keys()) == {"by_strategy", "by_symbol", "by_regime"}
