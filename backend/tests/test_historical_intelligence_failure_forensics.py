"""Edge-discovery investigation Phase 1 regression tests: failure forensics.

Covers: milestone reach-rate aggregation reuses the ALREADY-PERSISTED reached_*r booleans (never
recomputes them), INSUFFICIENT_SAMPLE below the floor, outcome categorization (WINNER/LOSER/
IMMEDIATE_FAILURE/LARGE_WINNER) is mutually exclusive and covers every trusted row, and feature
comparison correctly measures a real, deliberately-planted difference between winners and
immediate failures."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.historical_intelligence import failure_forensics as ff
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM
from backend.shared.db import Base

NOW = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    import backend.shared.db as shared_db

    monkeypatch.setattr(shared_db, "SessionLocal", SessionLocal)
    return SessionLocal


def _seed(db, *, idx: int, strategy: str = "mtfai1", symbol: str = "EURUSD", session: str = "LONDON", bos_present: bool = False,
          outcome_r: float = 1.0, immediate_failure: bool = False, reached_0_25r: bool = True, reached_0_5r: bool | None = None,
          reached_0_75r: bool = False, reached_1r: bool = False, reached_1_5r: bool = False, reached_2r: bool = False,
          mfe_r: float = 1.0, tp_hit: bool = False):
    entry_time = NOW + timedelta(hours=idx)
    db.add(HistoricalPatternFingerprintORM(
        fingerprint_id=f"FP{idx}", historical_intelligence_version="hi-v1", strategy_version="replay-v1", fingerprint_version="fp-v2",
        source_quality_tier="RECONSTRUCTED", provider="MT5", proxy=False, canonical_symbol=symbol, direction="LONG",
        anchor_strategy=strategy, contributing_strategies=[strategy], liquidity_location="none", fvg_state="none", order_block_state="none",
        spread_regime="unknown", session=session, bos_present=bos_present, entry=1.1, stop_loss=1.09, take_profit=1.12,
        entry_time=entry_time, peer_group_hash="H", created_at=entry_time,
    ))
    db.add(HistoricalSetupOutcomeORM(
        outcome_id=f"OUT{idx}", fingerprint_id=f"FP{idx}", resolution_status="RESOLVED", outcome_r=outcome_r, data_quality="HIGH", bars_scanned=5,
        created_at=entry_time, immediate_failure=immediate_failure, reached_0_25r=reached_0_25r, reached_0_5r=reached_0_5r if reached_0_5r is not None else reached_0_25r,
        reached_0_75r=reached_0_75r, reached_1r=reached_1r, reached_1_5r=reached_1_5r, reached_2r=reached_2r, mfe_r=mfe_r, mae_r=-0.5, tp_hit=tp_hit,
    ))


def test_milestone_reach_rates_uses_persisted_booleans_not_recomputed(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(12):
            _seed(db, idx=i, reached_0_25r=True, reached_1r=(i < 6))  # half reach 1R, all reach 0.25R
        db.commit()

    result = ff.milestone_reach_rates("anchor_strategy")
    group = result["groups"]["mtfai1"]
    assert group["n"] == 12
    assert group["reached_0_25r"] == 1.0
    assert group["reached_1r"] == 0.5


def test_milestone_reach_rates_insufficient_sample_below_floor(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(3):
            _seed(db, idx=i)
        db.commit()

    result = ff.milestone_reach_rates("anchor_strategy", min_sample=10)
    assert result["groups"]["mtfai1"]["status"] == "INSUFFICIENT_SAMPLE"


def test_never_reached_0_25r_pct_is_complement_of_reach_rate(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(10):
            _seed(db, idx=i, reached_0_25r=(i < 3))  # 30% reach, 70% never reach
        db.commit()

    result = ff.milestone_reach_rates("anchor_strategy")
    group = result["groups"]["mtfai1"]
    assert group["reached_0_25r"] == 0.3
    assert group["never_reached_0_25r_pct"] == 0.7


def test_categorization_is_mutually_exclusive_and_covers_every_row(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed(db, idx=0, outcome_r=1.0, immediate_failure=False, reached_1r=False)  # winner
        _seed(db, idx=1, outcome_r=-1.0, immediate_failure=False)  # loser
        _seed(db, idx=2, outcome_r=-1.0, immediate_failure=True)  # immediate failure
        _seed(db, idx=3, outcome_r=2.5, immediate_failure=False, reached_0_25r=True)
        db.commit()
        # large winner needs reached_2r=True -- set directly
        row = db.get(HistoricalSetupOutcomeORM, "OUT3")
        row.reached_2r = True
        db.commit()

    result = ff.feature_comparison(min_sample=1)
    assert result["categories"][ff.WINNER]["n"] == 1
    assert result["categories"][ff.LOSER]["n"] == 1
    assert result["categories"][ff.IMMEDIATE_FAILURE]["n"] == 1
    assert result["categories"][ff.LARGE_WINNER]["n"] == 1


def test_feature_comparison_measures_a_real_planted_difference(monkeypatch):
    """Winners all have bos_present=True; immediate failures all have bos_present=False -- the
    comparison must surface this as a real, measured 100% vs 0% difference."""
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(15):
            _seed(db, idx=i, outcome_r=1.0, immediate_failure=False, bos_present=True)
        for i in range(15, 30):
            _seed(db, idx=i, outcome_r=-1.0, immediate_failure=True, bos_present=False)
        db.commit()

    result = ff.feature_comparison()
    assert result["categories"][ff.WINNER]["pct_bos_present"] == 1.0
    assert result["categories"][ff.IMMEDIATE_FAILURE]["pct_bos_present"] == 0.0


def test_feature_comparison_insufficient_sample_per_category(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed(db, idx=0, outcome_r=1.0)
        db.commit()

    result = ff.feature_comparison(min_sample=5)
    assert result["categories"][ff.WINNER]["status"] == "INSUFFICIENT_SAMPLE"
    assert result["categories"][ff.LOSER]["n"] == 0


# --- profit_leak_funnel (Phase 2) --------------------------------------------------------------


def test_profit_leak_funnel_narrows_monotonically(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        # 10 trades: 4 never reach 0.25R, 3 reach 0.5R only, 3 reach 1R
        for i in range(4):
            _seed(db, idx=i, reached_0_25r=False, mfe_r=0.1, outcome_r=-1.0)
        for i in range(4, 7):
            _seed(db, idx=i, reached_0_25r=True, reached_0_5r=True, mfe_r=0.6, outcome_r=0.2)
        for i in range(7, 10):
            _seed(db, idx=i, reached_0_25r=True, reached_0_5r=True, reached_1r=True, mfe_r=1.2, outcome_r=0.8)
        db.commit()

    result = ff.profit_leak_funnel("mtfai1")
    counts = {stage["stage"]: stage["n"] for stage in result["funnel"]}
    assert counts["all"] == 10
    assert counts["reached_0_25r"] == 6
    assert counts["reached_0_5r"] == 6
    assert counts["reached_1r"] == 3
    assert counts["reached_1r"] <= counts["reached_0_5r"] <= counts["reached_0_25r"] <= counts["all"]


def test_1r_breakdown_partitions_correctly(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed(db, idx=0, reached_1r=True, outcome_r=1.5)  # realized >= 1R
        _seed(db, idx=1, reached_1r=True, outcome_r=0.7)  # 0.5-1R
        _seed(db, idx=2, reached_1r=True, outcome_r=0.2)  # 0-0.5R
        _seed(db, idx=3, reached_1r=True, outcome_r=-1.0)  # negative
        db.commit()

    result = ff.profit_leak_funnel("mtfai1")
    breakdown = result["at_least_1r_breakdown"]
    assert breakdown["n"] == 4
    assert breakdown["pct_realized_ge_1r"] == 0.25
    assert breakdown["pct_realized_0_5_to_1r"] == 0.25
    assert breakdown["pct_realized_0_to_0_5r"] == 0.25
    assert breakdown["pct_realized_negative"] == 0.25


# --- entry_failure_vs_giveback (Phase 3) --------------------------------------------------------


def test_entry_failure_and_giveback_costs_are_measured_separately(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        # 2 immediate failures (never reach 0.25R): -1R each = -2R entry-failure cost
        for i in range(2):
            _seed(db, idx=i, reached_0_25r=False, mfe_r=0.1, outcome_r=-1.0)
        # 2 trades that reached 1R (mfe=1.2) but only realized 0.2R each: 1.0R giveback each = 2.0R total
        for i in range(2, 4):
            _seed(db, idx=i, reached_0_25r=True, reached_1r=True, mfe_r=1.2, outcome_r=0.2)
        db.commit()

    result = ff.entry_failure_vs_giveback("mtfai1")
    assert result["entry_failure_cost_r"] == -2.0
    assert result["profit_giveback_cost_r"] == 2.0  # (1.2-0.2) * 2
    assert result["buckets"][ff.IMMEDIATE_FAILURE_BUCKET]["n"] == 2
    assert result["buckets"][ff.STRONG_WINNER]["n"] == 2


def test_bucket_membership_is_mutually_exclusive_and_covers_all(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed(db, idx=0, reached_0_25r=False)
        _seed(db, idx=1, reached_0_25r=True, reached_0_5r=False)
        _seed(db, idx=2, reached_0_25r=True, reached_0_5r=True, reached_1r=False)
        _seed(db, idx=3, reached_0_25r=True, reached_0_5r=True, reached_1r=True, reached_2r=False)
        _seed(db, idx=4, reached_0_25r=True, reached_0_5r=True, reached_1r=True, reached_2r=True)
        db.commit()

    result = ff.entry_failure_vs_giveback("mtfai1")
    total_bucketed = sum(b["n"] for b in result["buckets"].values())
    assert total_bucketed == 5
    assert result["buckets"][ff.IMMEDIATE_FAILURE_BUCKET]["n"] == 1
    assert result["buckets"][ff.WEAK_MOVE]["n"] == 1
    assert result["buckets"][ff.DEVELOPING_WINNER]["n"] == 1
    assert result["buckets"][ff.STRONG_WINNER]["n"] == 1
    assert result["buckets"][ff.LARGE_WINNER_BUCKET]["n"] == 1


# --- milestone_continuation_probabilities (Phase 7) ---------------------------------------------


def test_continuation_probabilities_computed_conditionally(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(6):
            _seed(db, idx=i, reached_0_5r=True, reached_1r=(i < 3), outcome_r=(1.0 if i < 3 else -0.5))
        db.commit()

    result = ff.milestone_continuation_probabilities("mtfai1")
    entry = result["conditional_probabilities"]["reached_0_5r"]
    assert entry["n"] == 6
    assert entry["probability_reached_1r"] == 0.5
    assert entry["probability_reversal_to_loss"] == 0.5
