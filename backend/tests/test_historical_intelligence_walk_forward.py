"""Phase 1 (Forex/MT5 roadmap) regression tests: chronological walk-forward splitting (no
leakage), purge/embargo boundary respected, deterministic edge_stability classification, and
persistence/latest-lookup for the entry_intelligence.py third gate."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.historical_intelligence import walk_forward
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM
from backend.shared.db import Base

NOW = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(walk_forward, "SessionLocal", SessionLocal)
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


# --- edge_stability classification (pure, deterministic) -------------------------------------


def test_classify_strong_when_oos_retains_most_of_train_edge():
    train = {"n": 50, "expectancy_r": 1.0}
    oos = {"n": 20, "expectancy_r": 0.8}  # retains 80%
    assert walk_forward.classify_edge_stability(train=train, oos=oos) == walk_forward.EDGE_STRONG


def test_classify_degraded_when_oos_retains_little_of_train_edge():
    train = {"n": 50, "expectancy_r": 1.0}
    oos = {"n": 20, "expectancy_r": 0.2}  # retains 20%
    assert walk_forward.classify_edge_stability(train=train, oos=oos) == walk_forward.EDGE_DEGRADED


def test_classify_failed_oos_when_edge_vanishes():
    train = {"n": 50, "expectancy_r": 1.0}
    oos = {"n": 20, "expectancy_r": -0.5}
    assert walk_forward.classify_edge_stability(train=train, oos=oos) == walk_forward.EDGE_FAILED_OOS


def test_classify_insufficient_sample_below_floor():
    train = {"n": 5, "expectancy_r": 1.0}
    oos = {"n": 20, "expectancy_r": 1.0}
    assert walk_forward.classify_edge_stability(train=train, oos=oos) == walk_forward.EDGE_INSUFFICIENT_SAMPLE


def test_classify_never_reports_strong_when_train_never_had_edge():
    """A positive OOS result when train itself was never profitable is reported ACCEPTABLE, not
    STRONG -- nothing has been proven to 'hold up' since there was no edge in the first place."""
    train = {"n": 50, "expectancy_r": -0.3}
    oos = {"n": 20, "expectancy_r": 0.5}
    assert walk_forward.classify_edge_stability(train=train, oos=oos) == walk_forward.EDGE_ACCEPTABLE


# --- chronological split integrity ------------------------------------------------------------


def test_split_is_chronological_never_shuffled(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    r_values = [1.0] * 30 + [-1.0] * 30  # first half all wins, second half all losses
    with SessionLocal() as db:
        _seed(db, n=60, strategy="test_strat", start=NOW, spacing=timedelta(hours=1), r_values=r_values)
        db.commit()

    result = walk_forward.run_walk_forward(anchor_strategy="test_strat", train_fraction=0.5, purge=timedelta(minutes=1))
    # Train (early, chronologically first) must be dominated by wins; OOS (late) by losses --
    # if splitting were shuffled/random this assertion would fail most of the time.
    assert result["train"]["win_rate"] > 0.8
    assert result["oos"]["win_rate"] < 0.2


def test_purge_window_excludes_boundary_rows(monkeypatch):
    """Rows within the purge gap of the split boundary must be excluded from BOTH train and
    OOS -- proving no leakage across the boundary."""
    SessionLocal = _session_factory(monkeypatch)
    r_values = [1.0] * 20
    with SessionLocal() as db:
        _seed(db, n=20, strategy="purge_test", start=NOW, spacing=timedelta(hours=1), r_values=r_values)
        db.commit()

    result = walk_forward.run_walk_forward(anchor_strategy="purge_test", train_fraction=0.5, purge=timedelta(hours=3))
    assert result["purged_n"] > 0
    assert result["train"]["n"] + result["oos"]["n"] + result["purged_n"] == result["total_n"]


# --- persistence / latest lookup for the entry_intelligence.py gate ---------------------------


def test_latest_edge_stability_reads_persisted_result(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    r_values = [1.0] * 30 + [0.9] * 30  # consistently positive, should retain most edge
    with SessionLocal() as db:
        _seed(db, n=60, strategy="persist_test", start=NOW, spacing=timedelta(hours=1), r_values=r_values)
        db.commit()

    walk_forward.run_walk_forward(anchor_strategy="persist_test", train_fraction=0.5, purge=timedelta(minutes=1))
    lookup = walk_forward.latest_edge_stability(anchor_strategy="persist_test")
    assert lookup["edge_stability"] in {walk_forward.EDGE_STRONG, walk_forward.EDGE_ACCEPTABLE}
    assert lookup["train_n"] > 0


def test_latest_edge_stability_insufficient_when_no_run_recorded(monkeypatch):
    _session_factory(monkeypatch)
    lookup = walk_forward.latest_edge_stability(anchor_strategy="never_run_strategy")
    assert lookup["edge_stability"] == walk_forward.EDGE_INSUFFICIENT_SAMPLE
