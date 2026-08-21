"""Strategy Robustness Report regression tests (QuantConnect/LEAN gap-analysis roadmap Phase 1,
items 2-7): PSR/DSR wiring, Monte Carlo reproducibility with a fixed seed, block-bootstrap
behavior, drawdown duration, recovery factor, strategy/symbol isolation, and walk-forward reuse.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.historical_intelligence import strategy_robustness as sr
from backend.historical_intelligence import walk_forward
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM
from backend.shared.db import Base

NOW = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(sr, "SessionLocal", SessionLocal)
    monkeypatch.setattr(walk_forward, "SessionLocal", SessionLocal)
    return SessionLocal


def _seed_outcome(db, *, idx: int, strategy: str, symbol: str, entry_time: datetime, gross_r: float, net_r: float | None,
                   spread_prov: str = "OBSERVED", commission_prov: str = "OBSERVED_ZERO"):
    fid = f"FP_{strategy}_{symbol}_{idx}"
    db.add(HistoricalPatternFingerprintORM(
        fingerprint_id=fid, historical_intelligence_version="hi-v1", strategy_version="replay-v1", fingerprint_version="fp-v2",
        source_quality_tier="RECONSTRUCTED", provider="MT5", proxy=False, canonical_symbol=symbol, direction="LONG",
        anchor_strategy=strategy, contributing_strategies=[strategy], liquidity_location="none", fvg_state="none",
        order_block_state="none", spread_regime="unknown", entry=1.1, stop_loss=1.09, take_profit=1.12,
        entry_time=entry_time, peer_group_hash=f"H_{fid}", created_at=entry_time,
    ))
    db.add(HistoricalSetupOutcomeORM(
        outcome_id=f"OUT_{fid}", fingerprint_id=fid, resolution_status="RESOLVED", data_quality="HIGH",
        outcome_r=gross_r, net_outcome_r=net_r, bars_scanned=5, spread_cost_provenance=spread_prov, commission_cost_provenance=commission_prov,
    ))


def _seed_r_sequence(db, *, strategy: str, symbol: str, values: list[float], start: datetime = NOW, step: timedelta = timedelta(days=1)):
    for i, r in enumerate(values):
        _seed_outcome(db, idx=i, strategy=strategy, symbol=symbol, entry_time=start + step * i, gross_r=r, net_r=r - 0.05)


# --- fetch / isolation --------------------------------------------------------------------------


def test_strategy_isolation(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed_r_sequence(db, strategy="breakout", symbol="EURUSD", values=[1.0, -1.0, 1.0])
        _seed_r_sequence(db, strategy="momentum", symbol="EURUSD", values=[2.0, 2.0])
        db.commit()

    breakout_rows = sr.fetch_strategy_rows("breakout")
    momentum_rows = sr.fetch_strategy_rows("momentum")
    assert len(breakout_rows) == 3
    assert len(momentum_rows) == 2
    assert all(r.gross_r in (1.0, -1.0) for r in breakout_rows)


def test_symbol_isolation(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed_r_sequence(db, strategy="breakout", symbol="EURUSD", values=[1.0, 1.0])
        _seed_r_sequence(db, strategy="breakout", symbol="GBPUSD", values=[-1.0])
        db.commit()

    eur_rows = sr.fetch_strategy_rows("breakout", canonical_symbol="EURUSD")
    gbp_rows = sr.fetch_strategy_rows("breakout", canonical_symbol="GBPUSD")
    assert len(eur_rows) == 2
    assert len(gbp_rows) == 1
    all_rows = sr.fetch_strategy_rows("breakout")
    assert len(all_rows) == 3


def test_untrusted_outcomes_excluded(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed_outcome(db, idx=0, strategy="breakout", symbol="EURUSD", entry_time=NOW, gross_r=1.0, net_r=0.9)
        fid = "FP_UNTRUSTED"
        db.add(HistoricalPatternFingerprintORM(
            fingerprint_id=fid, historical_intelligence_version="hi-v1", strategy_version="replay-v1", fingerprint_version="fp-v2",
            source_quality_tier="RECONSTRUCTED", provider="MT5", proxy=False, canonical_symbol="EURUSD", direction="LONG",
            anchor_strategy="breakout", contributing_strategies=["breakout"], liquidity_location="none", fvg_state="none",
            order_block_state="none", spread_regime="unknown", entry=1.1, stop_loss=1.09, take_profit=1.12,
            entry_time=NOW, peer_group_hash=f"H_{fid}", created_at=NOW,
        ))
        db.add(HistoricalSetupOutcomeORM(outcome_id=f"OUT_{fid}", fingerprint_id=fid, resolution_status="RESOLVED", data_quality="UNTRUSTED", outcome_r=5.0, bars_scanned=1))
        db.commit()

    rows = sr.fetch_strategy_rows("breakout")
    assert len(rows) == 1
    assert rows[0].gross_r == 1.0


# --- cost realism summary ------------------------------------------------------------------------


def test_cost_realism_summary_computes_drag_only_from_known_net_rows(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed_outcome(db, idx=0, strategy="s", symbol="EURUSD", entry_time=NOW, gross_r=2.0, net_r=1.8, spread_prov="OBSERVED")
        _seed_outcome(db, idx=1, strategy="s", symbol="EURUSD", entry_time=NOW + timedelta(days=1), gross_r=-1.0, net_r=-1.2, spread_prov="OBSERVED")
        _seed_outcome(db, idx=2, strategy="s", symbol="EURUSD", entry_time=NOW + timedelta(days=2), gross_r=1.0, net_r=None, spread_prov="UNKNOWN")
        db.commit()

    rows = sr.fetch_strategy_rows("s")
    summary = sr.cost_realism_summary(rows)
    assert summary["trades"] == 3
    assert summary["net_sample_size"] == 2
    assert summary["gross_expectancy_r"] == pytest.approx((2.0 - 1.0 + 1.0) / 3, abs=1e-4)
    assert summary["net_expectancy_r"] == pytest.approx((1.8 - 1.2) / 2, abs=1e-4)
    assert summary["cost_drag_r"] is not None
    assert summary["spread_provenance_pct"]["OBSERVED"] == pytest.approx(200 / 3, rel=1e-2)
    assert summary["spread_provenance_pct"]["UNKNOWN"] == pytest.approx(100 / 3, rel=1e-2)


def test_cost_realism_summary_empty():
    summary = sr.cost_realism_summary([])
    assert summary["trades"] == 0
    assert summary["gross_expectancy_r"] is None


# --- PSR/DSR wiring -------------------------------------------------------------------------------


def test_robustness_scorecard_wiring_returns_expected_keys(monkeypatch):
    import random
    rng = random.Random(7)
    values = [rng.gauss(0.15, 1.0) for _ in range(120)]
    times = [NOW + timedelta(days=i) for i in range(120)]
    result = sr.robustness_scorecard(values, times)
    for key in ("annual_sharpe", "psr", "dsr", "verdict", "bootstrap"):
        assert key in result
    assert result["verdict"] in ("robust", "fragile", "overfit", "insufficient")


def test_robustness_scorecard_insufficient_sample_flags_clearly():
    result = sr.robustness_scorecard([1.0, -1.0, 1.0], [NOW, NOW + timedelta(days=1), NOW + timedelta(days=2)])
    assert "insufficient_sample_note" in result


# --- Monte Carlo reproducibility + block bootstrap -------------------------------------------------


def test_monte_carlo_reproducible_with_fixed_seed():
    values = [1.0, -1.0, 0.5, -0.5, 1.5, -1.0, 2.0, -0.8] * 5  # n=40
    first = sr.monte_carlo_r_space(values, paths=200, seed=123)
    second = sr.monte_carlo_r_space(values, paths=200, seed=123)
    assert first["terminal_cumulative_r"] == second["terminal_cumulative_r"]
    assert first["drawdown_r_percentiles"] == second["drawdown_r_percentiles"]


def test_monte_carlo_different_seeds_differ():
    values = [1.0, -1.0, 0.5, -0.5, 1.5, -1.0, 2.0, -0.8] * 5
    a = sr.monte_carlo_r_space(values, paths=200, seed=1)
    b = sr.monte_carlo_r_space(values, paths=200, seed=2)
    assert a["terminal_cumulative_r"] != b["terminal_cumulative_r"]


def test_monte_carlo_insufficient_sample_skips_cleanly():
    result = sr.monte_carlo_r_space([1.0, -1.0], paths=100)
    assert result["paths"] == 0
    assert "note" in result


def test_monte_carlo_account_level_view_uses_real_config_when_given():
    values = [1.0, -1.0, 0.5, -0.5, 1.5, -1.0, 2.0, -0.8] * 5
    result = sr.monte_carlo_r_space(values, paths=200, seed=1, risk_percent_per_trade=0.25)
    assert "account_level_view" in result
    assert result["account_level_view"]["risk_percent_per_trade"] == 0.25
    assert 0.0 <= result["account_level_view"]["drawdown_pct_p95"] <= 100.0


def test_monte_carlo_never_uses_compounding_cumprod_on_minus_one_r():
    """A -1R loss must not collapse the R-space Monte Carlo to zero the way a naive cumprod(1+R)
    equity-curve interpretation would."""
    values = [-1.0, 1.0, 1.0, 1.0, -1.0, 1.0, 1.0, 1.0] * 5
    result = sr.monte_carlo_r_space(values, paths=500, seed=1)
    assert result["terminal_cumulative_r"]["p50"] != 0.0


# --- drawdown duration / recovery factor -----------------------------------------------------------


def test_drawdown_duration_and_recovery_factor_hand_calculated(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    # Sequence: +1, +1 (peak=2), -1, -1, -1 (trough=-1, depth=3), +1, +1, +1, +1 (recovers to 3, new peak)
    values = [1.0, 1.0, -1.0, -1.0, -1.0, 1.0, 1.0, 1.0, 1.0]
    with SessionLocal() as db:
        _seed_r_sequence(db, strategy="dd_test", symbol="EURUSD", values=values, start=NOW, step=timedelta(days=1))
        db.commit()

    rows = sr.fetch_strategy_rows("dd_test")
    stats = sr.drawdown_duration_stats(rows)
    assert stats["max_drawdown_r"] == pytest.approx(3.0, rel=1e-6)
    assert stats["longest_losing_streak"] == 3
    # cumulative final = sum(values) = 3.0; recovery_factor = final / max_dd = 3/3 = 1.0
    assert stats["recovery_factor"] == pytest.approx(1.0, rel=1e-6)
    assert stats["closed_drawdown_episode_count"] == 1
    assert stats["max_drawdown_duration_days"] is not None and stats["max_drawdown_duration_days"] > 0


def test_drawdown_duration_reports_unrecovered_episode(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    values = [1.0, -1.0, -1.0, -1.0]  # ends in drawdown, never recovers
    with SessionLocal() as db:
        _seed_r_sequence(db, strategy="dd_open", symbol="EURUSD", values=values, start=NOW, step=timedelta(days=1))
        db.commit()

    rows = sr.fetch_strategy_rows("dd_open")
    stats = sr.drawdown_duration_stats(rows)
    assert stats["closed_drawdown_episode_count"] == 0  # never recovered -> not a "closed" episode
    assert stats["currently_unrecovered_duration_days"] > 0


def test_drawdown_duration_empty():
    stats = sr.drawdown_duration_stats([])
    assert stats["max_drawdown_r"] is None
    assert stats["longest_losing_streak"] == 0


# --- effective sample size -------------------------------------------------------------------------


def test_effective_sample_size_near_n_for_uncorrelated_series():
    import random
    rng = random.Random(3)
    values = [rng.gauss(0, 1) for _ in range(200)]
    ess = sr.effective_sample_size(values)
    assert ess > 150  # should stay close to N=200 when there's no serial correlation


def test_effective_sample_size_reduced_for_correlated_series():
    # Strong positive autocorrelation: alternating long runs of the same sign.
    values = ([1.0] * 10 + [-1.0] * 10) * 5
    ess = sr.effective_sample_size(values)
    assert ess < len(values) * 0.5


# --- walk-forward reuse (read-only) ------------------------------------------------------------------


def test_walk_forward_verdict_insufficient_sample(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed_r_sequence(db, strategy="tiny", symbol="EURUSD", values=[1.0, -1.0, 1.0])
        db.commit()

    result = sr.walk_forward_verdict("tiny")
    assert result["verdict"] == "N/A"
    assert result["edge_stability"] == walk_forward.EDGE_INSUFFICIENT_SAMPLE


# --- full report assembly ---------------------------------------------------------------------------


def test_build_strategy_robustness_report_shape(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    import random
    rng = random.Random(11)
    values = [rng.gauss(0.1, 1.0) for _ in range(80)]
    with SessionLocal() as db:
        _seed_r_sequence(db, strategy="full_report", symbol="EURUSD", values=values)
        db.commit()

    report = sr.build_strategy_robustness_report("full_report", canonical_symbol="EURUSD")
    assert report["strategy"] == "full_report"
    assert report["historical_evidence"]["trades"] == 80
    assert "statistical_robustness" in report
    assert "monte_carlo" in report["statistical_robustness"]
    assert "drawdown" in report
    assert "walk_forward" in report
