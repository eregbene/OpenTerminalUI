from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.execution_analytics.services import ExecutionAnalyticsService
from backend.execution_analytics.slippage import calculate_slippage


def test_buy_and_sell_slippage_direction() -> None:
  assert calculate_slippage("BUY", 100, 101, 10)["basis_point_slippage"] == 100
  assert calculate_slippage("SELL", 100, 99, 10)["basis_point_slippage"] == 100


def test_execution_analytics_flags_partial_and_delayed_ack() -> None:
  now = datetime.now(timezone.utc)
  result = ExecutionAnalyticsService().analyze(
    {"side": "BUY", "quantity": 100, "decision_price": 100, "timestamp": now.isoformat()},
    [{"quantity": 50, "price": 102, "commission": 1}],
    {"broker_submission_started": now, "broker_acknowledged": now + timedelta(seconds=6)},
  )
  assert "PARTIAL_FILL" in result["anomalies"]
  assert "DELAYED_ACKNOWLEDGEMENT" in result["anomalies"]
  assert result["fill_quality"]["fill_ratio"] == 0.5
