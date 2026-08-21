"""Execution-cost realism regression tests (QuantConnect/LEAN gap-analysis roadmap Phase 1,
item 1) -- gross-vs-net R, the OBSERVED/HISTORICAL_ESTIMATE/CONFIG_FALLBACK/UNKNOWN spread
provenance tiers, commission provenance, no-look-ahead cost estimation, and zero/missing-cost
cases. Reuses outcomes.py's own future-candle fixture pattern (test_historical_intelligence_
phase4.py) rather than reinventing it."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.brokers.mt5.orm import MT5CanonicalCandleORM, MT5TradeRecordORM
from backend.historical_intelligence import execution_costs, outcomes
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM
from backend.shared.db import Base

NOW = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(outcomes, "SessionLocal", SessionLocal)
    monkeypatch.setattr(execution_costs, "SessionLocal", SessionLocal)
    execution_costs.invalidate_spread_index()
    execution_costs.invalidate_commission_cache()
    return SessionLocal


@pytest.fixture(autouse=True)
def _reset_caches(monkeypatch):
    execution_costs.invalidate_spread_index()
    execution_costs.invalidate_commission_cache()
    monkeypatch.delenv("MT5_HISTORICAL_SPREAD_FALLBACK_EURUSD", raising=False)
    monkeypatch.setenv("MT5_COMMISSION_MODE", "NONE")
    yield
    execution_costs.invalidate_spread_index()
    execution_costs.invalidate_commission_cache()


def _seed_future_bar(db, *, bar_time: datetime, high: float, low: float, close: float, symbol: str = "EURUSD"):
    db.add(MT5CanonicalCandleORM(
        candle_id=f"MT5:{symbol}:M15:{bar_time.isoformat()}", provider="MT5", canonical_symbol=symbol, broker_symbol=symbol,
        timeframe="M15", timestamp=bar_time, timestamp_utc=bar_time, open=close, high=high, low=low, close=close,
        quality="VALID", finalized=True, updated_at=bar_time + timedelta(days=1),
    ))


def _label(*, fingerprint_id: str, entry_time: datetime, real_spread: float | None = None, entry=1.1000, stop_loss=1.0980, take_profit=1.1200, symbol="EURUSD"):
    return outcomes.label_outcome(
        fingerprint_id=fingerprint_id, canonical_symbol=symbol, broker_symbol=symbol, direction="LONG",
        entry=entry, stop_loss=stop_loss, take_profit=take_profit, entry_time=entry_time, real_spread=real_spread,
    )


def _seed_fingerprint(db, *, fingerprint_id: str, entry_time: datetime, symbol="EURUSD", entry=1.1000, stop_loss=1.0980, take_profit=1.1200):
    """HistoricalSpreadIndex joins HistoricalSetupOutcomeORM to HistoricalPatternFingerprintORM
    (for canonical_symbol/entry_time) -- in real production pattern_builder.py always writes both
    rows together, so HISTORICAL_ESTIMATE tests must seed the fingerprint row too, not just call
    label_outcome() (which only persists the outcome row) in isolation."""
    db.add(HistoricalPatternFingerprintORM(
        fingerprint_id=fingerprint_id, historical_intelligence_version="hi-v1", strategy_version="replay-v1", fingerprint_version="fp-v2",
        source_quality_tier="RECONSTRUCTED", provider="MT5", proxy=False, canonical_symbol=symbol, direction="LONG",
        anchor_strategy="mtfai1", contributing_strategies=["mtfai1"], liquidity_location="none", fvg_state="none",
        order_block_state="none", spread_regime="unknown", entry=entry, stop_loss=stop_loss, take_profit=take_profit,
        entry_time=entry_time, peer_group_hash=f"H_{fingerprint_id}", created_at=entry_time,
    ))


# --- gross vs net, OBSERVED tier ------------------------------------------------------------------


def test_gross_r_always_preserved_regardless_of_cost_knowledge(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    entry_time = NOW
    with SessionLocal() as db:
        _seed_future_bar(db, bar_time=entry_time + timedelta(minutes=15), high=1.1300, low=1.0995, close=1.1250)
        db.commit()

    result = _label(fingerprint_id="FP_GROSS", entry_time=entry_time, real_spread=None)
    assert result["outcome_r"] is not None
    assert result["tp_hit"] is True
    assert result["net_outcome_r"] is None  # no cost could be honestly determined
    assert result["spread_cost_provenance"] == execution_costs.UNKNOWN


def test_observed_tier_deducts_real_spread_from_net_r(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    entry_time = NOW
    with SessionLocal() as db:
        _seed_future_bar(db, bar_time=entry_time + timedelta(minutes=15), high=1.1300, low=1.0995, close=1.1250)
        db.commit()

    result = _label(fingerprint_id="FP_OBS", entry_time=entry_time, real_spread=0.0004)
    risk = abs(1.1000 - 1.0980)
    assert result["spread_cost_provenance"] == execution_costs.OBSERVED
    assert result["real_spread_price"] == pytest.approx(0.0004)
    assert result["spread_cost_r"] == pytest.approx(0.0004 / risk, rel=1e-6)
    assert result["net_outcome_r"] == pytest.approx(result["outcome_r"] - 0.0004 / risk, rel=1e-6)


def test_zero_or_negative_real_spread_never_treated_as_observed(monkeypatch):
    """A real_spread of 0 or negative is not a genuine observation (matches outcomes.py's
    original _net_r guard) -- must fall through to the next tier, not be silently accepted."""
    SessionLocal = _session_factory(monkeypatch)
    entry_time = NOW
    with SessionLocal() as db:
        _seed_future_bar(db, bar_time=entry_time + timedelta(minutes=15), high=1.1300, low=1.0995, close=1.1250)
        db.commit()

    result = _label(fingerprint_id="FP_ZERO_SPREAD", entry_time=entry_time, real_spread=0.0)
    assert result["spread_cost_provenance"] != execution_costs.OBSERVED
    assert result["net_outcome_r"] is None


# --- HISTORICAL_ESTIMATE tier + no-look-ahead -----------------------------------------------------


def test_historical_estimate_uses_prior_real_observations_for_same_symbol(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    earlier = NOW - timedelta(days=10)
    later = NOW
    with SessionLocal() as db:
        _seed_future_bar(db, bar_time=earlier + timedelta(minutes=15), high=1.1300, low=1.0995, close=1.1250)
        _seed_future_bar(db, bar_time=later + timedelta(minutes=15), high=1.1300, low=1.0995, close=1.1250)
        _seed_fingerprint(db, fingerprint_id="FP_SEED_OBSERVED", entry_time=earlier)
        _seed_fingerprint(db, fingerprint_id="FP_ESTIMATE_TARGET", entry_time=later)
        db.commit()

    _label(fingerprint_id="FP_SEED_OBSERVED", entry_time=earlier, real_spread=0.0006)
    execution_costs.invalidate_spread_index()  # a fresh label_outcome() call may have built/cached an empty index before the seed row existed
    result = _label(fingerprint_id="FP_ESTIMATE_TARGET", entry_time=later, real_spread=None)

    risk = abs(1.1000 - 1.0980)
    assert result["spread_cost_provenance"] == execution_costs.HISTORICAL_ESTIMATE
    assert result["real_spread_price"] is None  # estimates are never persisted as if they were real observations
    assert result["spread_cost_r"] == pytest.approx(0.0006 / risk, rel=1e-6)
    assert result["net_outcome_r"] is not None


def test_historical_estimate_never_uses_observations_from_after_entry_time(monkeypatch):
    """No-look-ahead (Part 1's explicit requirement): a real spread observed AFTER this setup's
    own entry_time must never be used to estimate ITS cost, even though it is real data."""
    SessionLocal = _session_factory(monkeypatch)
    target_time = NOW
    future_observation_time = NOW + timedelta(days=5)
    with SessionLocal() as db:
        _seed_future_bar(db, bar_time=target_time + timedelta(minutes=15), high=1.1300, low=1.0995, close=1.1250)
        _seed_future_bar(db, bar_time=future_observation_time + timedelta(minutes=15), high=1.1300, low=1.0995, close=1.1250)
        _seed_fingerprint(db, fingerprint_id="FP_TARGET_FIRST", entry_time=target_time)
        _seed_fingerprint(db, fingerprint_id="FP_FUTURE_OBSERVATION", entry_time=future_observation_time)
        db.commit()

    _label(fingerprint_id="FP_TARGET_FIRST", entry_time=target_time, real_spread=None)
    _label(fingerprint_id="FP_FUTURE_OBSERVATION", entry_time=future_observation_time, real_spread=0.0009)
    execution_costs.invalidate_spread_index()
    # Re-label the earlier setup with a fresh index build now that a LATER real observation exists.
    result = _label(fingerprint_id="FP_TARGET_FIRST", entry_time=target_time, real_spread=None)

    assert result["spread_cost_provenance"] == execution_costs.UNKNOWN  # must NOT pick up the future 0.0009 observation
    assert result["net_outcome_r"] is None


def test_historical_estimate_is_symbol_scoped(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    earlier = NOW - timedelta(days=10)
    later = NOW
    with SessionLocal() as db:
        _seed_future_bar(db, bar_time=earlier + timedelta(minutes=15), high=1.1300, low=1.0995, close=1.1250, symbol="GBPUSD")
        _seed_future_bar(db, bar_time=later + timedelta(minutes=15), high=1.1300, low=1.0995, close=1.1250, symbol="EURUSD")
        _seed_fingerprint(db, fingerprint_id="FP_GBP_SEED", entry_time=earlier, symbol="GBPUSD")
        _seed_fingerprint(db, fingerprint_id="FP_EUR_TARGET", entry_time=later, symbol="EURUSD")
        db.commit()

    _label(fingerprint_id="FP_GBP_SEED", entry_time=earlier, real_spread=0.0006, symbol="GBPUSD")
    execution_costs.invalidate_spread_index()
    result = _label(fingerprint_id="FP_EUR_TARGET", entry_time=later, real_spread=None, symbol="EURUSD")

    assert result["spread_cost_provenance"] == execution_costs.UNKNOWN  # GBPUSD observation must not leak into EURUSD


# --- CONFIG_FALLBACK tier --------------------------------------------------------------------------


def test_config_fallback_used_only_when_explicitly_set(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    entry_time = NOW
    with SessionLocal() as db:
        _seed_future_bar(db, bar_time=entry_time + timedelta(minutes=15), high=1.1300, low=1.0995, close=1.1250)
        db.commit()

    monkeypatch.setenv("MT5_HISTORICAL_SPREAD_FALLBACK_EURUSD", "0.0005")
    result = _label(fingerprint_id="FP_CONFIG_FALLBACK", entry_time=entry_time, real_spread=None)
    risk = abs(1.1000 - 1.0980)
    assert result["spread_cost_provenance"] == execution_costs.CONFIG_FALLBACK
    assert result["spread_cost_r"] == pytest.approx(0.0005 / risk, rel=1e-6)


def test_config_fallback_off_by_default(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    entry_time = NOW
    with SessionLocal() as db:
        _seed_future_bar(db, bar_time=entry_time + timedelta(minutes=15), high=1.1300, low=1.0995, close=1.1250)
        db.commit()

    result = _label(fingerprint_id="FP_NO_FALLBACK", entry_time=entry_time, real_spread=None)
    assert result["spread_cost_provenance"] == execution_costs.UNKNOWN


# --- commission provenance --------------------------------------------------------------------------


def test_commission_none_mode_is_config_zero(monkeypatch):
    monkeypatch.setenv("MT5_COMMISSION_MODE", "NONE")
    execution_costs.invalidate_commission_cache()
    result = execution_costs.resolve_commission_cost_r()
    assert result.commission_cost_r == 0.0
    assert result.provenance == execution_costs.COMMISSION_CONFIG_ZERO


def test_commission_broker_reported_empirically_zero(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(execution_costs, "SessionLocal", SessionLocal)
    monkeypatch.setenv("MT5_COMMISSION_MODE", "BROKER_REPORTED")
    execution_costs.invalidate_commission_cache()

    with SessionLocal() as db:
        for i in range(25):
            db.add(MT5TradeRecordORM(
                trade_id=f"T{i}", account_id="demo_10k", cycle_id="C1", symbol="EURUSD", broker_symbol="EURUSD",
                direction="LONG", lot_size=0.1, commission=0.0, close_timestamp=NOW + timedelta(minutes=i),
            ))
        db.commit()

    result = execution_costs.resolve_commission_cost_r()
    assert result.commission_cost_r == 0.0
    assert result.provenance == execution_costs.COMMISSION_OBSERVED_ZERO


def test_commission_broker_reported_nonzero_is_unknown_not_guessed(monkeypatch):
    """A real nonzero commission cannot be converted to R-units without a lot-size assumption
    this module has no honest basis for -- must be UNKNOWN, never estimated."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(execution_costs, "SessionLocal", SessionLocal)
    monkeypatch.setenv("MT5_COMMISSION_MODE", "BROKER_REPORTED")
    execution_costs.invalidate_commission_cache()

    with SessionLocal() as db:
        for i in range(25):
            db.add(MT5TradeRecordORM(
                trade_id=f"T{i}", account_id="demo_10k", cycle_id="C1", symbol="EURUSD", broker_symbol="EURUSD",
                direction="LONG", lot_size=0.1, commission=-7.0, close_timestamp=NOW + timedelta(minutes=i),
            ))
        db.commit()

    result = execution_costs.resolve_commission_cost_r()
    assert result.commission_cost_r is None
    assert result.provenance == execution_costs.COMMISSION_UNKNOWN


def test_commission_insufficient_sample_is_unknown(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(execution_costs, "SessionLocal", SessionLocal)
    monkeypatch.setenv("MT5_COMMISSION_MODE", "BROKER_REPORTED")
    execution_costs.invalidate_commission_cache()

    result = execution_costs.resolve_commission_cost_r()
    assert result.commission_cost_r is None
    assert result.provenance == execution_costs.COMMISSION_UNKNOWN


def test_commission_cache_is_reused_until_invalidated(monkeypatch):
    monkeypatch.setenv("MT5_COMMISSION_MODE", "NONE")
    execution_costs.invalidate_commission_cache()
    first = execution_costs.resolve_commission_cost_r()
    monkeypatch.setenv("MT5_COMMISSION_MODE", "CONFIG_FALLBACK")  # changing env must NOT retroactively change a cached result
    second = execution_costs.resolve_commission_cost_r()
    assert first.provenance == second.provenance == execution_costs.COMMISSION_CONFIG_ZERO
    execution_costs.invalidate_commission_cache()
    third = execution_costs.resolve_commission_cost_r()
    assert third.provenance == execution_costs.COMMISSION_UNKNOWN  # now reflects the new mode


# --- net_outcome_r combines both components when both are known -------------------------------------


def test_net_r_subtracts_both_spread_and_commission_when_both_known(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(outcomes, "SessionLocal", SessionLocal)
    monkeypatch.setattr(execution_costs, "SessionLocal", SessionLocal)
    execution_costs.invalidate_spread_index()
    monkeypatch.setenv("MT5_COMMISSION_MODE", "BROKER_REPORTED")
    execution_costs.invalidate_commission_cache()

    with SessionLocal() as db:
        for i in range(25):
            db.add(MT5TradeRecordORM(
                trade_id=f"T{i}", account_id="demo_10k", cycle_id="C1", symbol="EURUSD", broker_symbol="EURUSD",
                direction="LONG", lot_size=0.1, commission=0.0, close_timestamp=NOW + timedelta(minutes=i),
            ))
        entry_time = NOW + timedelta(days=1)
        _seed_future_bar(db, bar_time=entry_time + timedelta(minutes=15), high=1.1300, low=1.0995, close=1.1250)
        db.commit()

    result = _label(fingerprint_id="FP_BOTH_COSTS", entry_time=entry_time, real_spread=0.0004)
    risk = abs(1.1000 - 1.0980)
    assert result["commission_cost_provenance"] == execution_costs.COMMISSION_OBSERVED_ZERO
    assert result["net_outcome_r"] == pytest.approx(result["outcome_r"] - 0.0004 / risk - 0.0, rel=1e-6)
