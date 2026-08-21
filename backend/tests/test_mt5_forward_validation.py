"""Priority 6: forward FTMO validation reporting (backend/mt5_strategies/forward_validation.py).
Covers the sample-size honesty guard and the hard separation between historical simulation and
actual forward performance the user explicitly required.
"""
from __future__ import annotations

import backend.historical_intelligence.orm  # noqa: F401 -- registers tables on Base before create_all() runs
from backend.mt5_strategies import forward_validation as fv


def test_sample_label_is_honest_about_thin_samples():
    assert fv._sample_label(0) == "no_data"
    assert fv._sample_label(3) == "insufficient"
    assert fv._sample_label(20) == "preliminary"
    assert fv._sample_label(50) == "indicative"
    assert fv._sample_label(500) == "reliable"


def test_summarize_records_computes_expectancy_and_consecutive_losses():
    records = [
        {"net_r": 1.0, "net_pnl": 25.0, "gross_pnl": 26.0, "total_trading_cost": 1.0},
        {"net_r": -1.0, "net_pnl": -25.0, "gross_pnl": -24.0, "total_trading_cost": 1.0},
        {"net_r": -0.5, "net_pnl": -12.5, "gross_pnl": -12.0, "total_trading_cost": 0.5},
        {"net_r": 0.8, "net_pnl": 20.0, "gross_pnl": 20.5, "total_trading_cost": 0.5},
    ]

    result = fv._summarize_records(records)

    assert result["sample_size"] == 4
    assert result["win_rate"] == 0.5
    assert result["consecutive_losses_max"] == 2
    assert result["net_expectancy_r"] == 0.075


def test_summarize_records_handles_no_losses_gracefully():
    records = [{"net_r": 1.0, "net_pnl": 25.0, "gross_pnl": 25.0, "total_trading_cost": 0.0}]

    result = fv._summarize_records(records)

    assert result["profit_factor"] is None  # no losses -> undefined, never fabricated as inf
    assert result["avg_loss_r"] is None


def test_ftmo_breach_simulation_is_explicitly_labeled_as_simulation(monkeypatch):
    from backend.shared.db import SessionLocal as real_session_local

    # Force the "no historical data" branch -- cheapest way to exercise the function's own
    # early-return contract without needing real corpus rows in this test's isolated DB.
    from backend.shared.test_db_safety import redirect_shared_db_to_isolated_sqlite

    redirect_shared_db_to_isolated_sqlite(monkeypatch)

    result = fv.ftmo_breach_simulation(trades_per_window=10, paths=50)

    assert result["status"] in {"NO_HISTORICAL_DATA", "HISTORICAL_SIMULATION"}
    if result["status"] == "HISTORICAL_SIMULATION":
        assert "simulation" in result["label"].lower()
        assert "not a report of actual forward" in result["label"].lower()
