from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PortfolioRiskLimits:
  max_daily_loss: float
  max_weekly_loss: float
  max_monthly_loss: float
  max_drawdown: float
  max_gross_exposure: float
  max_net_exposure: float
  max_leverage: float
  max_margin_utilization: float
  concentration_limit: float
  stale_data_seconds: int
