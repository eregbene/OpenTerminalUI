from __future__ import annotations

from backend.portfolio_risk.concentration import concentration_metrics
from backend.portfolio_risk.correlation import pearson
from backend.portfolio_risk.limits import evaluate_limits
from backend.portfolio_risk.models import PortfolioRiskLimits
from backend.portfolio_risk.var import historical_var_cvar


def test_concentration_and_limits_are_deterministic() -> None:
  concentration = concentration_metrics({"AAPL": 60, "MSFT": 40}, 100)
  assert concentration["largest"] == {"key": "AAPL", "weight": 0.6}
  limits = PortfolioRiskLimits(100, 500, 1000, 2000, 90, 80, 1.0, 0.5, 0.4, 300)
  status = evaluate_limits({"gross_exposure": 100, "net_exposure": 20, "leverage": 1.2}, limits)
  assert status["status"] == "BREACH"
  assert "GROSS_EXPOSURE" in status["breaches"]
  assert status["auto_close_positions"] is False


def test_correlation_requires_minimum_sample() -> None:
  assert pearson([1, 2], [1, 2])["status"] == "INSUFFICIENT_SAMPLE"
  assert pearson([1, 2, 3], [1, 2, 3])["correlation"] == 1


def test_var_labels_insufficient_samples_and_limitations() -> None:
  result = historical_var_cvar([-0.01, 0.02])
  assert result["status"] == "INSUFFICIENT_SAMPLE"
  assert "not a maximum possible loss" in " ".join(result["limitations"])
