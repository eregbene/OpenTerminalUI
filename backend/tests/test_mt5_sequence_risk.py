"""Phase 2 (Forex/MT5 roadmap) regression tests: Monte Carlo / sequence-risk lab.

Covers: sustainability classification thresholds, INSUFFICIENT_SAMPLE never fabricates a
distribution, R-multiple pool source priority (real account trades > pooled historical proxy >
insufficient), the real per-position R computation matches compute_trade_costs's net_pnl /
original_risk_money exactly, and the compounding-equity simulation behaves sanely at both
extremes (all-winning and all-catastrophic-losing R pools)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.adaptive_management.orm import AdaptivePositionStateORM, AdaptiveTradeEventORM
from backend.brokers.mt5 import sequence_risk
from backend.historical_intelligence import walk_forward
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM, HistoricalWalkForwardResultORM
from backend.shared.db import Base

NOW = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(sequence_risk, "SessionLocal", SessionLocal)
    return SessionLocal


# --- classify_sustainability (pure) ------------------------------------------------------------


def test_classify_sustainable_when_both_probabilities_low():
    assert sequence_risk.classify_sustainability(probability_of_ruin=0.01, probability_daily_loss_breach=0.05) == sequence_risk.SUSTAINABLE


def test_classify_marginal_at_moderate_ruin_probability():
    assert sequence_risk.classify_sustainability(probability_of_ruin=0.08, probability_daily_loss_breach=0.05) == sequence_risk.MARGINAL


def test_classify_unsustainable_at_high_ruin_probability():
    assert sequence_risk.classify_sustainability(probability_of_ruin=0.25, probability_daily_loss_breach=0.05) == sequence_risk.UNSUSTAINABLE


def test_classify_unsustainable_at_high_daily_breach_probability():
    assert sequence_risk.classify_sustainability(probability_of_ruin=0.0, probability_daily_loss_breach=0.60) == sequence_risk.UNSUSTAINABLE


# --- simulate: insufficient sample never fabricates -----------------------------------------


def test_simulate_insufficient_sample_returns_unknown_without_simulating():
    result = sequence_risk.simulate(account_id="demo_10k", r_multiples=[], r_source=sequence_risk.SOURCE_INSUFFICIENT_SAMPLE, initial_balance=10000.0)
    assert result.sustainability == sequence_risk.UNKNOWN
    assert result.num_paths == 0
    assert result.max_drawdown_percentiles == {}


# --- simulate: sanity at extremes -------------------------------------------------------------


def test_simulate_all_winning_pool_never_breaches():
    winning = [1.5] * 50
    result = sequence_risk.simulate(account_id="demo_10k", r_multiples=winning, r_source=sequence_risk.SOURCE_PROXY_HISTORICAL_CORPUS, initial_balance=10000.0, num_paths=200, trades_per_path=50, seed=1)
    assert result.probability_of_ruin == 0.0
    assert result.probability_daily_loss_breach == 0.0
    assert result.sustainability == sequence_risk.SUSTAINABLE


def test_simulate_catastrophic_losing_pool_breaches_frequently():
    catastrophic = [-8.0] * 50  # a pool of trades each losing 8x the intended risk
    result = sequence_risk.simulate(account_id="demo_10k", r_multiples=catastrophic, r_source=sequence_risk.SOURCE_PROXY_HISTORICAL_CORPUS, initial_balance=10000.0, num_paths=200, trades_per_path=50, seed=1)
    assert result.probability_of_ruin > 0.9
    assert result.sustainability == sequence_risk.UNSUSTAINABLE


def test_simulate_reports_which_r_source_was_used():
    result = sequence_risk.simulate(account_id="demo_10k", r_multiples=[0.5, -1.0] * 20, r_source=sequence_risk.SOURCE_REAL_ACCOUNT_TRADES, initial_balance=10000.0, num_paths=50, trades_per_path=20, seed=1)
    assert result.r_source == sequence_risk.SOURCE_REAL_ACCOUNT_TRADES
    assert result.sample_size == 40


def test_simulate_is_deterministic_given_a_seed():
    pool = [0.5, -1.0, 2.0, -0.5] * 10
    a = sequence_risk.simulate(account_id="demo_10k", r_multiples=pool, r_source=sequence_risk.SOURCE_PROXY_HISTORICAL_CORPUS, initial_balance=10000.0, num_paths=100, trades_per_path=30, seed=7)
    b = sequence_risk.simulate(account_id="demo_10k", r_multiples=pool, r_source=sequence_risk.SOURCE_PROXY_HISTORICAL_CORPUS, initial_balance=10000.0, num_paths=100, trades_per_path=30, seed=7)
    assert a.max_drawdown_percentiles == b.max_drawdown_percentiles
    assert a.probability_of_ruin == b.probability_of_ruin


# --- r_multiple_pool: source priority -----------------------------------------------------------


def test_pool_prefers_real_trades_when_sufficient(monkeypatch):
    monkeypatch.setattr(sequence_risk, "real_closed_trade_r_multiples", lambda account_id: [0.1] * 40)
    monkeypatch.setattr(sequence_risk, "proxy_historical_r_multiples", lambda **kwargs: (_ for _ in ()).throw(AssertionError("proxy should not be queried when real trades are sufficient")))
    values, source = sequence_risk.r_multiple_pool("demo_10k")
    assert source == sequence_risk.SOURCE_REAL_ACCOUNT_TRADES
    assert len(values) == 40


def test_pool_falls_back_to_proxy_when_real_insufficient(monkeypatch):
    monkeypatch.setattr(sequence_risk, "real_closed_trade_r_multiples", lambda account_id: [0.1] * 5)
    monkeypatch.setattr(sequence_risk, "proxy_historical_r_multiples", lambda **kwargs: [0.2] * 100)
    values, source = sequence_risk.r_multiple_pool("demo_10k")
    assert source == sequence_risk.SOURCE_PROXY_HISTORICAL_CORPUS
    assert len(values) == 100


def test_pool_insufficient_when_both_sources_too_small(monkeypatch):
    monkeypatch.setattr(sequence_risk, "real_closed_trade_r_multiples", lambda account_id: [0.1] * 5)
    monkeypatch.setattr(sequence_risk, "proxy_historical_r_multiples", lambda **kwargs: [0.2] * 5)
    values, source = sequence_risk.r_multiple_pool("demo_10k")
    assert source == sequence_risk.SOURCE_INSUFFICIENT_SAMPLE
    assert values == []


# --- real_closed_trade_r_multiples: matches compute_trade_costs exactly ------------------------


def test_real_closed_trade_r_multiples_matches_net_pnl_over_original_risk(monkeypatch):
    _session_factory(monkeypatch)
    with sequence_risk.SessionLocal() as db:
        db.add(AdaptivePositionStateORM(
            position_id="P1", account_id="demo_10k", symbol="EURUSD", direction="LONG", broker_ticket="1",
            entry_price=1.1000, original_risk_money=100.0, closed_detected_at=NOW, contaminated=False,
        ))
        db.add(AdaptiveTradeEventORM(
            event_id="E1", account_id="demo_10k", session_id="S1", position_id="P1", event_type="DEAL",
            symbol="EURUSD", volume=0.1, realized_pnl=250.0, commission=-5.0, swap=-1.0, fee=0.0,
        ))
        db.commit()

    r_values = sequence_risk.real_closed_trade_r_multiples("demo_10k")
    assert len(r_values) == 1
    assert r_values[0] == pytest.approx((250.0 - 5.0 - 1.0) / 100.0)


def test_real_closed_trade_r_multiples_skips_positions_without_risk_or_deals(monkeypatch):
    _session_factory(monkeypatch)
    with sequence_risk.SessionLocal() as db:
        db.add(AdaptivePositionStateORM(
            position_id="P2", account_id="demo_10k", symbol="EURUSD", direction="LONG", broker_ticket="2",
            entry_price=1.1000, original_risk_money=None, closed_detected_at=NOW, contaminated=False,
        ))
        db.commit()

    assert sequence_risk.real_closed_trade_r_multiples("demo_10k") == []


def test_real_closed_trade_r_multiples_excludes_contaminated_positions(monkeypatch):
    _session_factory(monkeypatch)
    with sequence_risk.SessionLocal() as db:
        db.add(AdaptivePositionStateORM(
            position_id="P3", account_id="demo_10k", symbol="EURUSD", direction="LONG", broker_ticket="3",
            entry_price=1.1000, original_risk_money=100.0, closed_detected_at=NOW, contaminated=True,
        ))
        db.add(AdaptiveTradeEventORM(
            event_id="E3", account_id="demo_10k", session_id="S1", position_id="P3", event_type="DEAL",
            symbol="EURUSD", volume=0.1, realized_pnl=999.0, commission=0.0, swap=0.0, fee=0.0,
        ))
        db.commit()

    assert sequence_risk.real_closed_trade_r_multiples("demo_10k") == []


def test_proxy_historical_r_multiples_excludes_untrusted_and_unresolved(monkeypatch):
    _session_factory(monkeypatch)
    with sequence_risk.SessionLocal() as db:
        for i, (status, quality, outcome_r) in enumerate([
            ("RESOLVED", "HIGH", 1.0),
            ("RESOLVED", "UNTRUSTED", 999.0),  # excluded: untrusted quality
            ("PENDING", "HIGH", 2.0),  # excluded: not resolved
        ]):
            db.add(HistoricalPatternFingerprintORM(
                fingerprint_id=f"FP{i}", historical_intelligence_version="hi-v1", strategy_version="replay-v1", fingerprint_version="fp-v2",
                source_quality_tier="RECONSTRUCTED", provider="MT5", proxy=False, canonical_symbol="EURUSD", direction="LONG",
                anchor_strategy="mtfai1", contributing_strategies=["mtfai1"], liquidity_location="none", fvg_state="none", order_block_state="none",
                spread_regime="unknown", entry=1.1, stop_loss=1.09, take_profit=1.12, entry_time=NOW, peer_group_hash="H", created_at=NOW,
            ))
            db.add(HistoricalSetupOutcomeORM(outcome_id=f"OUT{i}", fingerprint_id=f"FP{i}", resolution_status=status, outcome_r=outcome_r, data_quality=quality, bars_scanned=5, created_at=NOW))
        db.commit()

    values = sequence_risk.proxy_historical_r_multiples()
    assert values == [1.0]


# --- Phase G: negative-combination exclusion --------------------------------------------------


def test_proxy_excludes_only_rows_belonging_to_failed_oos_combinations(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setattr(walk_forward, "SessionLocal", SessionLocal)
    with SessionLocal() as db:
        for i, (strategy, symbol, r) in enumerate([
            ("mtfai1", "USDJPY", -1.0),  # belongs to a FAILED_OOS combination -- must be excluded
            ("mtfai1", "USDJPY", -0.8),  # same combination -- also excluded
            ("mean_reversion", "EURUSD", 1.5),  # clean combination -- must be kept
        ]):
            db.add(HistoricalPatternFingerprintORM(
                fingerprint_id=f"FP{i}", historical_intelligence_version="hi-v1", strategy_version="replay-v1", fingerprint_version="fp-v2",
                source_quality_tier="RECONSTRUCTED", provider="MT5", proxy=False, canonical_symbol=symbol, direction="LONG",
                anchor_strategy=strategy, contributing_strategies=[strategy], liquidity_location="none", fvg_state="none", order_block_state="none",
                spread_regime="unknown", entry=1.1, stop_loss=1.09, take_profit=1.12, entry_time=NOW, peer_group_hash="H", created_at=NOW,
            ))
            db.add(HistoricalSetupOutcomeORM(outcome_id=f"OUT{i}", fingerprint_id=f"FP{i}", resolution_status="RESOLVED", outcome_r=r, data_quality="HIGH", bars_scanned=5, created_at=NOW))
        # A persisted combination-level FAILED_OOS result for mtfai1+USDJPY, matching exactly
        # what walk_forward.run_walk_forward(anchor_strategy=..., canonical_symbol=...) writes.
        db.add(HistoricalWalkForwardResultORM(
            result_id="R1", anchor_strategy="mtfai1", canonical_symbol="USDJPY", edge_stability="FAILED_OOS",
            total_n=50, train_n=30, oos_n=20, train_expectancy_r=-0.5, oos_expectancy_r=-0.6, train_stats={}, oos_stats={},
        ))
        db.commit()

    values = sequence_risk.proxy_historical_r_multiples_excluding_negative_combinations()
    assert sorted(values) == [1.5]  # only the clean mean_reversion+EURUSD row survives
