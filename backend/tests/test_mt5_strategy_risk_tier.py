"""Bensim -- Activate All Strategy Families in DEMO (Part 3): strategy-specific DEMO risk
tiers. MT5AutonomousTradingService._strategy_tier_risk_factor is a pure, sync function of
candidate["context"]["strategy_id"] + env overrides -- no I/O, no mocking needed."""
from __future__ import annotations

import backend.brokers.mt5.autonomous as autonomous_module
from backend.brokers.mt5.autonomous import MT5AutonomousTradingService
from backend.tests.test_mt5_adapter import fake_adapter


def _candidate(strategy_id: str) -> dict:
    return {"context": {"strategy_id": strategy_id}}


def _service() -> MT5AutonomousTradingService:
    return MT5AutonomousTradingService(fake_adapter())


def test_tier_a_strategies_get_full_risk():
    service = _service()
    for strategy_id in ("mtfai1", "trend_pullback", "mean_reversion"):
        factor, detail = service._strategy_tier_risk_factor(_candidate(strategy_id))
        assert factor == 1.0
        assert detail["tier"] == "A"


def test_tier_b_strategies_get_half_risk():
    service = _service()
    for strategy_id in ("vwap_reversion", "liquidity_sweep_reversal", "smc_continuation"):
        factor, detail = service._strategy_tier_risk_factor(_candidate(strategy_id))
        assert factor == 0.5
        assert detail["tier"] == "B"


def test_tier_c_strategies_get_quarter_risk():
    service = _service()
    for strategy_id in ("support_resistance_bounce", "breakout", "ema_trend", "session_breakout"):
        factor, detail = service._strategy_tier_risk_factor(_candidate(strategy_id))
        assert factor == 0.25
        assert detail["tier"] == "C"


def test_untiered_strategy_defaults_to_tier_a_not_silently_zero():
    """A strategy_id this table doesn't recognize (e.g. a future addition never wired in here)
    must default to unchanged (1.0x) risk, never silently under-size or crash."""
    service = _service()
    factor, detail = service._strategy_tier_risk_factor(_candidate("some_future_strategy"))
    assert factor == 1.0
    assert detail["tier"] == "A"


def test_missing_strategy_id_defaults_to_tier_a():
    service = _service()
    factor, detail = service._strategy_tier_risk_factor({"context": {}})
    assert factor == 1.0
    assert detail["tier"] == "A"


def test_tier_multiplier_is_reversible_via_env(monkeypatch):
    monkeypatch.setenv("MT5_STRATEGY_RISK_TIER_B_MULTIPLIER", "0.75")
    service = _service()
    factor, detail = service._strategy_tier_risk_factor(_candidate("vwap_reversion"))
    assert factor == 0.75
    assert detail["multiplier"] == 0.75


def test_tier_multiplier_is_clamped_to_valid_range(monkeypatch):
    monkeypatch.setenv("MT5_STRATEGY_RISK_TIER_C_MULTIPLIER", "5.0")
    service = _service()
    factor, _ = service._strategy_tier_risk_factor(_candidate("breakout"))
    assert factor == 1.0  # clamped, never amplifies risk above 1.0x

    monkeypatch.setenv("MT5_STRATEGY_RISK_TIER_C_MULTIPLIER", "-1.0")
    factor2, _ = service._strategy_tier_risk_factor(_candidate("breakout"))
    assert factor2 == 0.0  # clamped, never negative
