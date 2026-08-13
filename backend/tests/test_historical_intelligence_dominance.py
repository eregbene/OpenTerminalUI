"""Edge-quality investigation Phase E regression tests: strategy dominance/confirmation
analysis. Covers: standalone vs independently-confirmed fire classification (never
double-counting a row into both buckets), momentum-confirmation excludes momentum evaluating
itself, SMC-confirmation split by the real boolean columns, and fire_rate_of_total summing to 1
across standalone+confirmed."""
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


def _seed(db, *, idx: int, contributing: list[str], regime: str = "trending_up", symbol: str = "EURUSD", session: str = "LONDON", bos_present: bool = False, outcome_r: float = 1.0):
    entry_time = NOW + timedelta(hours=idx)
    db.add(HistoricalPatternFingerprintORM(
        fingerprint_id=f"FP{idx}", historical_intelligence_version="hi-v1", strategy_version="replay-v1", fingerprint_version="fp-v2",
        source_quality_tier="RECONSTRUCTED", provider="MT5", proxy=False, canonical_symbol=symbol, direction="LONG",
        anchor_strategy="mtfai1", contributing_strategies=contributing, liquidity_location="none", fvg_state="none", order_block_state="none",
        spread_regime="unknown", regime=regime, session=session, bos_present=bos_present, entry=1.1, stop_loss=1.09, take_profit=1.12,
        entry_time=entry_time, peer_group_hash="H", created_at=entry_time,
    ))
    db.add(HistoricalSetupOutcomeORM(outcome_id=f"OUT{idx}", fingerprint_id=f"FP{idx}", resolution_status="RESOLVED", outcome_r=outcome_r, data_quality="HIGH", bars_scanned=5, created_at=entry_time))


def test_standalone_and_confirmed_partition_without_overlap(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(5):
            _seed(db, idx=i, contributing=["mtfai1"])  # standalone
        for i in range(5, 10):
            _seed(db, idx=i, contributing=["mtfai1", "momentum"])  # confirmed
        db.commit()

    result = segment_matrix.strategy_dominance_analysis("mtfai1")
    assert result["total_fires"] == 10
    assert result["standalone"]["fire_count"] == 5
    assert result["independently_confirmed"]["fire_count"] == 5
    assert result["standalone"]["fire_rate_of_total"] + result["independently_confirmed"]["fire_rate_of_total"] == 1.0


def test_momentum_confirmation_excludes_momentum_evaluating_itself(monkeypatch):
    """When the anchor strategy IS momentum, "with_momentum_confirmation" must never count its
    own fires as self-confirmation, even though "momentum" trivially appears in its own
    contributing_strategies list."""
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        entry_time = NOW
        db.add(HistoricalPatternFingerprintORM(
            fingerprint_id="FPM", historical_intelligence_version="hi-v1", strategy_version="replay-v1", fingerprint_version="fp-v2",
            source_quality_tier="RECONSTRUCTED", provider="MT5", proxy=False, canonical_symbol="EURUSD", direction="LONG",
            anchor_strategy="momentum", contributing_strategies=["momentum"], liquidity_location="none", fvg_state="none", order_block_state="none",
            spread_regime="unknown", regime="trending_up", session="LONDON", entry=1.1, stop_loss=1.09, take_profit=1.12,
            entry_time=entry_time, peer_group_hash="H", created_at=entry_time,
        ))
        db.add(HistoricalSetupOutcomeORM(outcome_id="OUTM", fingerprint_id="FPM", resolution_status="RESOLVED", outcome_r=1.0, data_quality="HIGH", bars_scanned=5, created_at=entry_time))
        db.commit()

    result = segment_matrix.strategy_dominance_analysis("momentum")
    assert result["with_momentum_confirmation"]["fire_count"] == 0


def test_momentum_confirmation_counted_only_when_momentum_is_a_contributor(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(4):
            _seed(db, idx=i, contributing=["mtfai1", "momentum"])
        for i in range(4, 8):
            _seed(db, idx=i, contributing=["mtfai1"])
        db.commit()

    result = segment_matrix.strategy_dominance_analysis("mtfai1")
    assert result["with_momentum_confirmation"]["fire_count"] == 4


def test_smc_confirmation_split_by_real_boolean_columns(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(3):
            _seed(db, idx=i, contributing=["mtfai1"], bos_present=True)
        for i in range(3, 6):
            _seed(db, idx=i, contributing=["mtfai1"], bos_present=False)
        db.commit()

    result = segment_matrix.strategy_dominance_analysis("mtfai1")
    assert result["with_smc_confirmation"]["fire_count"] == 3
    assert result["without_smc_confirmation"]["fire_count"] == 3


def test_by_symbol_regime_session_breakdowns_cover_all_real_values(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed(db, idx=0, contributing=["mtfai1"], symbol="EURUSD", regime="trending_up", session="LONDON")
        _seed(db, idx=1, contributing=["mtfai1"], symbol="GBPUSD", regime="ranging", session="NY_OVERLAP")
        db.commit()

    result = segment_matrix.strategy_dominance_analysis("mtfai1")
    assert set(result["by_symbol"].keys()) == {"EURUSD", "GBPUSD"}
    assert set(result["by_regime"].keys()) == {"trending_up", "ranging"}
    assert set(result["by_session"].keys()) == {"LONDON", "NY_OVERLAP"}
