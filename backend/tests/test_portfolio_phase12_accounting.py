from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.portfolio.accounting import FillInput, build_average_cost_positions, cash_from_fills, stable_hash
from backend.portfolio.allocations import AllocationRequest, validate_allocation
from backend.portfolio.errors import AllocationValidationError
from backend.portfolio.models import AllocationType, PortfolioStatus
from backend.portfolio.valuation import Mark, value_positions


def test_duplicate_execution_is_not_counted_twice() -> None:
  fills = [
    FillInput(order_id="o1", symbol="AAPL", side="BUY", quantity=10, price=100, commission=1),
    FillInput(order_id="o1", symbol="AAPL", side="BUY", quantity=10, price=100, commission=1),
  ]
  positions = build_average_cost_positions(fills)
  assert positions["AAPL"].quantity == 10
  assert cash_from_fills(10_000, fills) == 8999


def test_average_cost_realized_and_unrealized_pnl() -> None:
  fills = [
    FillInput(order_id="o1", symbol="AAPL", side="BUY", quantity=10, price=100, commission=1),
    FillInput(order_id="o2", symbol="AAPL", side="BUY", quantity=10, price=110, commission=1),
    FillInput(order_id="o3", symbol="AAPL", side="SELL", quantity=5, price=120, commission=1),
  ]
  positions = build_average_cost_positions(fills)
  assert positions["AAPL"].quantity == 15
  assert positions["AAPL"].average_cost == 105
  assert positions["AAPL"].realized_pnl == 74
  valuation = value_positions(
    positions,
    {"AAPL": Mark(symbol="AAPL", price=130, timestamp=datetime.now(timezone.utc))},
    {("USD", "USD"): 1},
    "USD",
  )
  assert valuation["valuation_status"] == "COMPLETE"
  assert valuation["unrealized_pnl"] == 375


def test_missing_mark_creates_incomplete_valuation_status() -> None:
  positions = build_average_cost_positions([FillInput(order_id="o1", symbol="MSFT", side="BUY", quantity=2, price=50)])
  valuation = value_positions(positions, {}, {}, "USD")
  assert valuation["valuation_status"] == "MISSING_MARK"


def test_allocation_requires_active_paper_approved_deployment_and_limits() -> None:
  req = AllocationRequest(
    portfolio_status=PortfolioStatus.ACTIVE.value,
    deployment_approved=True,
    account_paper=True,
    portfolio_equity=100_000,
    allocation_type=AllocationType.FIXED_CAPITAL.value,
    allocation_amount=25_000,
    max_gross_exposure=30_000,
    max_net_exposure=25_000,
    max_daily_loss=1_000,
    max_drawdown=5_000,
  )
  assert validate_allocation(req)["valid"] is True


def test_invalid_allocation_is_not_silently_normalized() -> None:
  with pytest.raises(AllocationValidationError):
    validate_allocation(
      AllocationRequest(
        portfolio_status=PortfolioStatus.ACTIVE.value,
        deployment_approved=False,
        account_paper=True,
        portfolio_equity=100,
        allocation_type=AllocationType.FIXED_CAPITAL.value,
        allocation_amount=1_000,
        max_gross_exposure=0,
        max_net_exposure=0,
        max_daily_loss=0,
        max_drawdown=0,
      )
    )


def test_snapshot_hash_is_stable_and_changes_with_correction_payload() -> None:
  base = stable_hash({"portfolio_id": "p1", "equity": 100})
  same = stable_hash({"equity": 100, "portfolio_id": "p1"})
  corrected = stable_hash({"portfolio_id": "p1", "equity": 101, "supersedes": base})
  assert base == same
  assert corrected != base
