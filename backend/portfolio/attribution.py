from __future__ import annotations


RESIDUAL_REASONS = {
  "UNASSIGNED_STRATEGY",
  "MISSING_EXECUTION_LINK",
  "MISSING_MARK",
  "FX_CONVERSION_DIFFERENCE",
  "BROKER_ADJUSTMENT",
  "COMMISSION_TIMING",
  "ROUNDING",
  "UNKNOWN",
}


def attribute_pnl(entries: list[dict], portfolio_total: float) -> dict:
  by_strategy: dict[str, float] = {}
  by_instrument: dict[str, float] = {}
  explained = 0.0
  residuals: dict[str, float] = {}
  for entry in entries:
    pnl = float(entry.get("pnl") or entry.get("amount") or 0.0)
    strategy = str(entry.get("strategy_id") or "UNASSIGNED_STRATEGY")
    instrument = str(entry.get("instrument_id") or entry.get("symbol") or "UNKNOWN")
    reason = str(entry.get("residual_reason") or "")
    by_strategy[strategy] = by_strategy.get(strategy, 0.0) + pnl
    by_instrument[instrument] = by_instrument.get(instrument, 0.0) + pnl
    if reason in RESIDUAL_REASONS:
      residuals[reason] = residuals.get(reason, 0.0) + pnl
    else:
      explained += pnl
  residual = round(float(portfolio_total) - explained, 8)
  if abs(residual) > 1e-7:
    residuals["UNKNOWN"] = residuals.get("UNKNOWN", 0.0) + residual
  return {
    "by_strategy": {key: round(value, 8) for key, value in by_strategy.items()},
    "by_instrument": {key: round(value, 8) for key, value in by_instrument.items()},
    "residuals": {key: round(value, 8) for key, value in residuals.items()},
    "reconciled": abs(sum(by_strategy.values()) - float(portfolio_total)) <= 1e-7,
  }
