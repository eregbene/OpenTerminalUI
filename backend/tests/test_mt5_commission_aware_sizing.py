"""2026-08-18: user-requested -- calculate_risk_size (backend/brokers/mt5/execution.py, the
single canonical lot-sizing/risk-reward function every live order funnels through) previously
computed loss-per-lot and reward purely from price movement, with zero awareness of real broker
commission. Confirmed via grep: no reference to "commission" anywhere in execution.py or
risk_calculator.py before this fix -- the exact gap an earlier external forensic report on this
account flagged (net P&L evaporating under a realistic $7/lot commission model).

Fix: commission_per_lot_round_turn() (backend/brokers/mt5/trading_costs.py's own configured
rate, single source of truth, same figure the post-trade cost accounting reconciles against) is
now folded into loss_per_lot for sizing/minimum-volume checks, and subtracted from the reward side
before the risk:reward ratio is computed -- so a marginal setup that only clears its RR threshold
before real costs now correctly gets rejected.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from backend.brokers.mt5.execution import MT5ExecutionService
from backend.tests.test_mt5_multi_account_risk_protection_fix import FakeExecutionAdapter, _fake_symbol


def _svc() -> MT5ExecutionService:
    return MT5ExecutionService(FakeExecutionAdapter("demo_10k", login=1))


def test_estimated_commission_usd_matches_configured_rate_times_volume(monkeypatch):
    monkeypatch.setenv("MT5_COMMISSION_PER_STANDARD_LOT_ROUND_TURN", "7.0")
    svc = _svc()
    symbol = _fake_symbol()

    result = _run(svc, symbol, equity="100000", entry="4400", stop="4380", target="4460")

    assert result.status == "APPROVED"
    assert result.estimated_commission_usd == (Decimal("7.0") * result.volume).quantize(Decimal("0.01"))


def test_marginal_rr_setup_rejects_once_commission_is_included(monkeypatch):
    # Zero-commission control: RR exactly 1.5 (the floor) should approve with no cost drag.
    monkeypatch.setenv("MT5_COMMISSION_PER_STANDARD_LOT_ROUND_TURN", "0")
    svc = _svc()
    symbol = _fake_symbol()
    zero_cost = _run(svc, symbol, equity="25000", entry="4400", stop="4380", target="4430")
    assert zero_cost.status == "APPROVED"

    # Same exact geometry, real commission -- must now reject on cost-adjusted RR.
    monkeypatch.setenv("MT5_COMMISSION_PER_STANDARD_LOT_ROUND_TURN", "7.0")
    svc2 = _svc()
    with_cost = _run(svc2, symbol, equity="25000", entry="4400", stop="4380", target="4430")
    assert with_cost.status == "REJECTED"
    assert "RISK_REWARD_TOO_LOW" in with_cost.reasons


def test_higher_commission_reduces_volume_for_identical_setup(monkeypatch):
    symbol = _fake_symbol()

    monkeypatch.setenv("MT5_COMMISSION_PER_STANDARD_LOT_ROUND_TURN", "0")
    low_cost = _run(_svc(), symbol, equity="100000", entry="4400", stop="4380", target="4460")

    monkeypatch.setenv("MT5_COMMISSION_PER_STANDARD_LOT_ROUND_TURN", "500")  # large enough to cross a volume_step boundary
    high_cost = _run(_svc(), symbol, equity="100000", entry="4400", stop="4380", target="4460")

    assert low_cost.status == "APPROVED"
    assert high_cost.status == "APPROVED"
    assert high_cost.raw_volume < low_cost.raw_volume
    assert high_cost.volume < low_cost.volume


def test_real_total_loss_never_exceeds_risk_budget_once_commission_included(monkeypatch):
    monkeypatch.setenv("MT5_COMMISSION_PER_STANDARD_LOT_ROUND_TURN", "7.0")
    result = _run(_svc(), _fake_symbol(), equity="10000", entry="4400", stop="4380", target="4460")

    assert result.status == "APPROVED"
    real_total_loss = result.projected_loss_usd  # already commission-inclusive
    assert real_total_loss <= result.effective_risk_usd + Decimal("0.02")


def test_zero_commission_env_reproduces_pre_fix_behavior(monkeypatch):
    monkeypatch.setenv("MT5_COMMISSION_PER_STANDARD_LOT_ROUND_TURN", "0")
    result = _run(_svc(), _fake_symbol(), equity="25000", entry="4400", stop="4380", target="4430")

    assert result.status == "APPROVED"
    assert result.estimated_commission_usd == Decimal("0")
    assert result.risk_reward == pytest.approx(Decimal("1.5"), rel=1e-3)


def _run(svc, symbol, *, equity, entry, stop, target):
    import asyncio

    return asyncio.run(
        svc.calculate_risk_size(
            account_equity=Decimal(equity),
            symbol=symbol,
            direction="LONG",
            entry=Decimal(entry),
            stop=Decimal(stop),
            target=Decimal(target),
        )
    )
