from __future__ import annotations

from backend.portfolio.exposure import calculate_exposure
from backend.portfolio_risk.concentration import concentration_metrics
from backend.portfolio_risk.limits import evaluate_limits
from backend.portfolio_risk.models import PortfolioRiskLimits
from backend.portfolio_risk.stress import apply_percentage_shock
from backend.portfolio_risk.var import historical_var_cvar


class PortfolioRiskService:
  def snapshot(self, positions: list[dict], returns: list[float] | None = None, limits: PortfolioRiskLimits | None = None) -> dict:
    exposure = calculate_exposure(positions)
    var = historical_var_cvar(returns or [])
    concentration = concentration_metrics(exposure["by_instrument"], exposure["gross_exposure"])
    metrics = {**exposure, "leverage": 0.0}
    limit_status = evaluate_limits(metrics, limits) if limits else {"status": "NOT_CONFIGURED", "breaches": []}
    return {
      "exposure": exposure,
      "concentration": concentration,
      "var": var,
      "cvar": {"status": var["status"], "value": var["cvar"], "method": var["method"]},
      "stress": apply_percentage_shock(positions, -0.02),
      "limits": limit_status,
    }
