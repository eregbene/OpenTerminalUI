from __future__ import annotations

from backend.portfolio_risk.models import PortfolioRiskLimits


def evaluate_limits(metrics: dict, limits: PortfolioRiskLimits) -> dict:
  breaches = []
  if abs(float(metrics.get("daily_pnl", 0.0))) > limits.max_daily_loss and float(metrics.get("daily_pnl", 0.0)) < 0:
    breaches.append("DAILY_LOSS")
  if float(metrics.get("gross_exposure", 0.0)) > limits.max_gross_exposure:
    breaches.append("GROSS_EXPOSURE")
  if abs(float(metrics.get("net_exposure", 0.0))) > limits.max_net_exposure:
    breaches.append("NET_EXPOSURE")
  if float(metrics.get("leverage", 0.0)) > limits.max_leverage:
    breaches.append("LEVERAGE")
  return {
    "status": "BREACH" if breaches else "OK",
    "breaches": breaches,
    "allowed_actions": ["warn", "block_new_paper_submissions", "pause_strategy_deployment", "portfolio_emergency_state"] if breaches else [],
    "auto_close_positions": False,
  }
