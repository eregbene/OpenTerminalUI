from __future__ import annotations

from copy import deepcopy

from backend.research.prop_firms.engine import (
    apply_trade,
    best_day_share,
    consistency_status,
    daily_loss_amount,
    initial_account_state,
    monte_carlo,
    simulate_profile,
    total_loss_amount,
)
from backend.research.prop_firms.models import InternalSafetyProfile
from backend.research.prop_firms.profiles import EXECUTION_SCENARIOS, default_profiles
from backend.research.prop_firms.service import PropFirmLabService


def profile(profile_id: str = "FTMO_2_STEP_PHASE_1"):
    return default_profiles(100000)[profile_id]


def candidate(idx: int, *, day: str = "2026-07-27", r: float = 1.8) -> dict:
    return {
        "candidate_id": f"ffff{idx:012x}",
        "timestamp": f"{day}T08:{idx % 60:02d}:00+00:00",
        "day": day,
        "weekday": "Monday",
        "session": "London",
        "regime": "TRENDING",
        "strategy": "EMA Trend",
        "direction": "LONG",
        "confidence": 0.72,
        "risk_reward": 1.8,
        "r_multiple": r,
        "overnight": False,
    }


def test_daily_and_total_loss_include_floating_equity_when_profile_requires_it():
    p = profile()
    state = initial_account_state(p)
    state.current_balance = 99000
    state.current_equity = 97000
    state.daily_closed_pnl = -1000
    state.floating_pnl = -2000

    assert daily_loss_amount(state, p) == 3000
    assert total_loss_amount(state, p) == 3000


def test_static_and_trailing_drawdown_are_distinct():
    p = profile()
    state = initial_account_state(p)
    state.intraday_high_equity = 105000
    state.current_equity = 99000
    trailing = deepcopy(p)
    object.__setattr__(trailing, "static_or_trailing_drawdown", "TRAILING")

    assert total_loss_amount(state, p) == 1000
    assert total_loss_amount(state, trailing) == 6000


def test_minimum_days_profitable_days_and_best_day_rule():
    p = default_profiles(100000)["FTMO_1_STEP"]
    state = initial_account_state(p)
    state.day_pnls = {"2026-07-27": 8000, "2026-07-28": 1000}
    state.trading_days = 2
    state.profitable_days = 2

    assert round(best_day_share(state.day_pnls), 2) == 88.89
    assert consistency_status(state, p) == "CONSISTENCY_NOT_MET"


def test_internal_limit_stops_before_firm_limit_and_trade_caps_work():
    p = profile()
    internal = InternalSafetyProfile(risk_per_trade_percent=0.25, max_total_drawdown_percent=4, max_trades_per_day=1, max_consecutive_losses=10)
    candidates = [candidate(1, r=-1), candidate(2, r=-1), candidate(3, day="2026-07-28", r=-1)]
    result = simulate_profile(profile=p, internal=internal, candidates=candidates, risk_percent=0.5, trade_cap=2, scenario=EXECUTION_SCENARIOS["NORMAL"], policy="test")

    assert len(result["accepted_trades"]) == 2
    assert result["rejected_trades"] == 1
    assert result["state"]["current_balance"] > 90000


def test_execution_costs_affect_results():
    p = profile()
    internal = InternalSafetyProfile(risk_per_trade_percent=0.25, max_consecutive_losses=10)
    sample = [candidate(1, r=1)]
    normal = simulate_profile(profile=p, internal=internal, candidates=sample, risk_percent=0.25, trade_cap=1, scenario=EXECUTION_SCENARIOS["NORMAL"], policy="test")
    severe = simulate_profile(profile=p, internal=internal, candidates=sample, risk_percent=0.25, trade_cap=1, scenario=EXECUTION_SCENARIOS["SEVERE"], policy="test")

    assert severe["profit"] < normal["profit"]


def test_phase_pass_failure_outcomes_are_deterministic():
    p = profile()
    internal = InternalSafetyProfile(risk_per_trade_percent=0.5, max_consecutive_losses=10)
    wins = [candidate(idx, day=f"2026-07-{27 + idx:02d}", r=2.2) for idx in range(1, 8)]
    first = simulate_profile(profile=p, internal=internal, candidates=wins, risk_percent=0.5, trade_cap=1, scenario=EXECUTION_SCENARIOS["NORMAL"], policy="test")
    second = simulate_profile(profile=p, internal=internal, candidates=wins, risk_percent=0.5, trade_cap=1, scenario=EXECUTION_SCENARIOS["NORMAL"], policy="test")

    assert first["status"] == second["status"]
    assert first["profit"] == second["profit"]


def test_monte_carlo_seeded_reproducibility():
    attempt = {"accepted_trades": [candidate(1, r=1), candidate(2, r=-1)], "state": {"trading_days": 2}}

    assert monte_carlo(attempt, runs=100, seed=7) == monte_carlo(attempt, runs=100, seed=7)


def test_service_simulation_is_read_only_and_makes_zero_external_calls(monkeypatch):
    import backend.research.prop_firms.service as module

    state = {"ai_auto_paper_acceptance": {"entry_used": False, "complete": False}}
    monkeypatch.setattr(module, "get_state", lambda key: deepcopy(state.get(key, {})))
    monkeypatch.setattr(module, "set_state", lambda key, value: state.__setitem__(key, deepcopy(value)))

    async def fake_chart(symbol, interval, range_str):
        return {"candles": [], "source_symbol": "EURUSD=X"}

    monkeypatch.setattr(module.forex_service, "get_pair_chart", fake_chart)
    service = PropFirmLabService()
    import asyncio

    result = asyncio.run(
        service.create_simulation(
            {
                "profile_ids": ["FTMO_2_STEP_PHASE_1"],
                "risk_per_trade_percents": [0.1],
                "trade_frequency_caps": [1],
                "execution_scenarios": ["NORMAL"],
                "monte_carlo_runs": 10,
            }
        )
    )

    assert result["read_only"] is True
    assert result["provider_calls"] == 0
    assert result["ibkr_calls"] == 0
    assert result["report"]["safety_verification"]["live_acceptance_state_unchanged"] is True
    assert state["ai_auto_paper_acceptance"] == {"entry_used": False, "complete": False}
