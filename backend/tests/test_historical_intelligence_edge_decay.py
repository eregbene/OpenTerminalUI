"""Phase 7 (Forex/MT5 roadmap) regression tests: rolling-window edge decay detection.

Covers: INSUFFICIENT_RECENT_SAMPLE below the sample floors, FAILED when no long-term edge ever
existed, FAILED when both the 20- and 50-trade windows have gone negative (persistent decay),
DECAY_WARNING when only the 20-trade window has gone negative (early signal), and the
WEAKENING/EDGE_STABLE retained-fraction boundaries."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.historical_intelligence import edge_decay, walk_forward
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


def _seed(db, *, n: int, strategy: str, start: datetime, spacing: timedelta, r_values: list[float]):
    for i in range(n):
        entry_time = start + spacing * i
        db.add(HistoricalPatternFingerprintORM(
            fingerprint_id=f"FP_{strategy}_{i}", historical_intelligence_version="hi-v1", strategy_version="replay-v1", fingerprint_version="fp-v2",
            source_quality_tier="RECONSTRUCTED", provider="MT5", proxy=False, canonical_symbol="EURUSD", direction="LONG",
            anchor_strategy=strategy, contributing_strategies=[strategy], liquidity_location="none", fvg_state="none", order_block_state="none",
            spread_regime="unknown", entry=1.1, stop_loss=1.09, take_profit=1.12, entry_time=entry_time, peer_group_hash="H", created_at=entry_time,
        ))
        db.add(HistoricalSetupOutcomeORM(outcome_id=f"OUT_{strategy}_{i}", fingerprint_id=f"FP_{strategy}_{i}", resolution_status="RESOLVED", outcome_r=r_values[i], data_quality="HIGH", bars_scanned=5, created_at=entry_time))


# --- classify_decay (pure) ------------------------------------------------------------------


def test_insufficient_recent_sample_below_floor():
    long_term = {"n": 5, "expectancy_r": 1.0}
    recent = {20: {"n": 5, "expectancy_r": 1.0}, 50: {"n": 5, "expectancy_r": 1.0}}
    assert edge_decay.classify_decay(long_term=long_term, recent_by_window=recent) == edge_decay.INSUFFICIENT_RECENT_SAMPLE


def test_failed_when_no_long_term_edge_ever_existed():
    long_term = {"n": 100, "expectancy_r": -0.3}
    recent = {20: {"n": 20, "expectancy_r": 0.5}, 50: {"n": 50, "expectancy_r": 0.5}}
    assert edge_decay.classify_decay(long_term=long_term, recent_by_window=recent) == edge_decay.FAILED


def test_failed_when_both_recent_windows_negative():
    long_term = {"n": 200, "expectancy_r": 1.0}
    recent = {20: {"n": 20, "expectancy_r": -0.5}, 50: {"n": 50, "expectancy_r": -0.2}}
    assert edge_decay.classify_decay(long_term=long_term, recent_by_window=recent) == edge_decay.FAILED


def test_decay_warning_when_only_20_window_negative():
    long_term = {"n": 200, "expectancy_r": 1.0}
    recent = {20: {"n": 20, "expectancy_r": -0.1}, 50: {"n": 50, "expectancy_r": 0.6}}
    assert edge_decay.classify_decay(long_term=long_term, recent_by_window=recent) == edge_decay.DECAY_WARNING


def test_edge_stable_when_retained_fraction_high():
    long_term = {"n": 200, "expectancy_r": 1.0}
    recent = {20: {"n": 20, "expectancy_r": 0.8}, 50: {"n": 50, "expectancy_r": 0.9}}
    assert edge_decay.classify_decay(long_term=long_term, recent_by_window=recent) == edge_decay.EDGE_STABLE


def test_weakening_at_moderate_retained_fraction():
    long_term = {"n": 200, "expectancy_r": 1.0}
    recent = {20: {"n": 20, "expectancy_r": 0.5}, 50: {"n": 50, "expectancy_r": 0.6}}
    assert edge_decay.classify_decay(long_term=long_term, recent_by_window=recent) == edge_decay.WEAKENING


def test_decay_warning_at_low_retained_fraction():
    long_term = {"n": 200, "expectancy_r": 1.0}
    recent = {20: {"n": 20, "expectancy_r": 0.1}, 50: {"n": 50, "expectancy_r": 0.6}}
    assert edge_decay.classify_decay(long_term=long_term, recent_by_window=recent) == edge_decay.DECAY_WARNING


# --- strategy_edge_decay: real rolling-window query -------------------------------------------


def test_strategy_edge_decay_detects_recent_decay_after_strong_long_run(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    r_values = [1.0] * 180 + [-1.0] * 20  # strong long history, decayed in the most recent 20
    with SessionLocal() as db:
        _seed(db, n=200, strategy="decaying_strat", start=NOW, spacing=timedelta(hours=1), r_values=r_values)
        db.commit()

    result = edge_decay.strategy_edge_decay("decaying_strat")
    assert result["long_term"]["n"] == 200
    assert result["recent"]["last_20"]["n"] == 20
    assert result["recent"]["last_20"]["expectancy_r"] == -1.0
    assert result["edge_decay_status"] in {edge_decay.FAILED, edge_decay.DECAY_WARNING}


def test_strategy_edge_decay_stable_when_recent_matches_long_term(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    r_values = [1.0] * 200  # consistently positive throughout
    with SessionLocal() as db:
        _seed(db, n=200, strategy="stable_strat", start=NOW, spacing=timedelta(hours=1), r_values=r_values)
        db.commit()

    result = edge_decay.strategy_edge_decay("stable_strat")
    assert result["edge_decay_status"] == edge_decay.EDGE_STABLE


def test_all_strategies_edge_decay_covers_every_distinct_strategy(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed(db, n=25, strategy="strat_a", start=NOW, spacing=timedelta(hours=1), r_values=[1.0] * 25)
        _seed(db, n=25, strategy="strat_b", start=NOW, spacing=timedelta(hours=1), r_values=[-1.0] * 25)
        db.commit()

    result = edge_decay.all_strategies_edge_decay()
    assert set(result.keys()) == {"strat_a", "strat_b"}
