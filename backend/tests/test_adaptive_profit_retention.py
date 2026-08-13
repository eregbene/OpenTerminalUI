"""Phase 11 (Forex/MT5 roadmap) regression tests: milestone-segmented profit-retention policy
comparison. Covers: milestone filtering by REAL achieved MFE-R (not the hypothetical/policy R),
expected-additional-R computed against the baseline ON THE SAME trade subset, contaminated
positions excluded, insufficient-sample flagging, and best_policy_per_milestone never selecting
a policy below the sample floor."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.adaptive_management import profit_retention_analysis as pra
from backend.adaptive_management import service as adaptive_service
from backend.adaptive_management.orm import AdaptivePositionStateORM, CounterfactualOutcomeORM, TradePathSnapshotORM
from backend.shared.db import Base

NOW = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(adaptive_service, "SessionLocal", SessionLocal)
    import backend.shared.db as shared_db

    monkeypatch.setattr(shared_db, "SessionLocal", SessionLocal)
    return SessionLocal


def _seed_trade(db, *, trade_id: str, max_achieved_r: float, baseline_r: float, policy_r: dict[str, float], contaminated: bool = False):
    db.add(AdaptivePositionStateORM(position_id=trade_id, account_id="demo_10k", symbol="EURUSD", direction="LONG", broker_ticket=trade_id, entry_price=1.1, contaminated=contaminated))
    db.add(TradePathSnapshotORM(path_id=f"PATH_{trade_id}", trade_id=trade_id, symbol="EURUSD", direction="LONG", max_achieved_r=max_achieved_r))
    db.add(CounterfactualOutcomeORM(outcome_id=f"OUT_{trade_id}_{pra.BASELINE_POLICY_ID}", trade_id=trade_id, policy_id=pra.BASELINE_POLICY_ID, hypothetical_r=baseline_r, mfe_capture=0.5, applicable=True))
    for policy_id, r in policy_r.items():
        db.add(CounterfactualOutcomeORM(outcome_id=f"OUT_{trade_id}_{policy_id}", trade_id=trade_id, policy_id=policy_id, hypothetical_r=r, mfe_capture=0.8, applicable=True))


def test_milestone_filters_by_real_achieved_mfe_not_policy_r(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        # 12 trades that genuinely reached +1R MFE, 12 that only reached +0.4R
        for i in range(12):
            _seed_trade(db, trade_id=f"T_HIGH_{i}", max_achieved_r=1.2, baseline_r=0.5, policy_r={"trailing_atr_1_5_v1": 0.9})
        for i in range(12):
            _seed_trade(db, trade_id=f"T_LOW_{i}", max_achieved_r=0.4, baseline_r=0.2, policy_r={"trailing_atr_1_5_v1": 0.3})
        db.commit()

    result = pra.milestone_policy_comparison()
    at_1r = result["milestones"]["mfe_at_least_1.0R"]
    assert at_1r["trades_reaching_milestone"] == 12  # only the high-MFE trades qualify
    at_0_25r = result["milestones"]["mfe_at_least_0.25R"]
    assert at_0_25r["trades_reaching_milestone"] == 24  # all trades reached at least 0.25R


def test_expected_additional_r_computed_against_same_subset_baseline(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(15):
            _seed_trade(db, trade_id=f"T_{i}", max_achieved_r=1.5, baseline_r=0.5, policy_r={"breakeven_at_0_75r_v1": 1.0})
        db.commit()

    result = pra.milestone_policy_comparison()
    stats = result["milestones"]["mfe_at_least_1.0R"]["policies"]["breakeven_at_0_75r_v1"]
    assert stats["avg_hypothetical_r"] == 1.0
    assert stats["expected_additional_r_vs_baseline"] == 0.5  # 1.0 - 0.5 baseline
    assert stats["insufficient_sample"] is False


def test_baseline_reports_zero_additional_r_against_itself(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(15):
            _seed_trade(db, trade_id=f"T_{i}", max_achieved_r=1.0, baseline_r=0.6, policy_r={})
        db.commit()

    result = pra.milestone_policy_comparison()
    baseline_stats = result["milestones"]["mfe_at_least_1.0R"]["policies"][pra.BASELINE_POLICY_ID]
    assert baseline_stats["expected_additional_r_vs_baseline"] == 0.0


def test_contaminated_positions_excluded(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(15):
            _seed_trade(db, trade_id=f"T_CLEAN_{i}", max_achieved_r=1.0, baseline_r=0.5, policy_r={})
        _seed_trade(db, trade_id="T_CONTAMINATED", max_achieved_r=1.0, baseline_r=999.0, policy_r={}, contaminated=True)
        db.commit()

    result = pra.milestone_policy_comparison()
    baseline_stats = result["milestones"]["mfe_at_least_1.0R"]["policies"][pra.BASELINE_POLICY_ID]
    assert baseline_stats["sample_size"] == 15  # the contaminated trade's baseline row is excluded
    assert baseline_stats["avg_hypothetical_r"] == 0.5  # not pulled toward 999.0


def test_insufficient_sample_flagged_below_min_sample(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(3):
            _seed_trade(db, trade_id=f"T_{i}", max_achieved_r=1.0, baseline_r=0.5, policy_r={"trailing_atr_1_5_v1": 0.9})
        db.commit()

    result = pra.milestone_policy_comparison(min_sample=10)
    stats = result["milestones"]["mfe_at_least_1.0R"]["policies"]["trailing_atr_1_5_v1"]
    assert stats["sample_size"] == 3
    assert stats["insufficient_sample"] is True


def test_best_policy_per_milestone_never_selects_below_sample_floor(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        # policy A: huge edge but only 3 samples (must be excluded)
        for i in range(3):
            _seed_trade(db, trade_id=f"T_A_{i}", max_achieved_r=1.0, baseline_r=0.5, policy_r={"policy_a": 5.0})
        # policy B: modest edge, 15 samples (must be selected)
        for i in range(15):
            _seed_trade(db, trade_id=f"T_B_{i}", max_achieved_r=1.0, baseline_r=0.5, policy_r={"policy_b": 0.7})
        db.commit()

    best = pra.best_policy_per_milestone(pra.milestone_policy_comparison(min_sample=10))
    assert best["mfe_at_least_1.0R"]["best_policy_id"] == "policy_b"


def test_no_qualifying_policy_reports_insufficient_sample(monkeypatch):
    _session_factory(monkeypatch)
    result = pra.milestone_policy_comparison()
    best = pra.best_policy_per_milestone(result)
    assert best["mfe_at_least_1.0R"]["best_policy_id"] is None
    assert best["mfe_at_least_1.0R"]["reason"] == "INSUFFICIENT_SAMPLE"
