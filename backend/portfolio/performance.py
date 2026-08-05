from __future__ import annotations

from math import sqrt
from statistics import mean, pstdev

from backend.portfolio.drawdown import EquityPoint, calculate_drawdown


WINDOWS = {
  "TODAY",
  "WEEK_TO_DATE",
  "MONTH_TO_DATE",
  "QUARTER_TO_DATE",
  "YEAR_TO_DATE",
  "ROLLING_7D",
  "ROLLING_30D",
  "ROLLING_90D",
  "SINCE_INCEPTION",
  "CUSTOM",
}


def returns_from_equity(points: list[EquityPoint]) -> list[float]:
  ordered = sorted(points, key=lambda row: row.t)
  out = []
  for prev, cur in zip(ordered, ordered[1:]):
    if prev.equity != 0:
      out.append(cur.equity / prev.equity - 1.0)
  return out


def calculate_performance(points: list[EquityPoint], trades: list[dict] | None = None, annualization: int = 252) -> dict:
  ordered = sorted(points, key=lambda row: row.t)
  trades = trades or []
  if len(ordered) < 2:
    return {
      "sample_count": len(ordered),
      "data_completeness": "INSUFFICIENT_SAMPLE",
      "minimum_sample_requirement": 2,
    }
  rets = returns_from_equity(ordered)
  total_return = ordered[-1].equity / ordered[0].equity - 1.0 if ordered[0].equity else 0.0
  vol = pstdev(rets) * sqrt(annualization) if len(rets) > 1 else 0.0
  avg = mean(rets) if rets else 0.0
  downside = [row for row in rets if row < 0]
  downside_dev = pstdev(downside) * sqrt(annualization) if len(downside) > 1 else 0.0
  wins = [float(t.get("pnl", 0.0)) for t in trades if float(t.get("pnl", 0.0)) > 0]
  losses = [float(t.get("pnl", 0.0)) for t in trades if float(t.get("pnl", 0.0)) < 0]
  gross_profit = sum(wins)
  gross_loss = abs(sum(losses))
  return {
    "sample_count": len(ordered),
    "calculation_window": "CUSTOM",
    "sampling_frequency": "event",
    "annualization_assumption": annualization,
    "data_completeness": "COMPLETE",
    "minimum_sample_requirement": 2,
    "total_return_pct": round(total_return * 100.0, 8),
    "net_return_pct": round(total_return * 100.0, 8),
    "gross_return_pct": round(total_return * 100.0, 8),
    "annualized_return_pct": round((((1.0 + total_return) ** (annualization / max(len(rets), 1))) - 1.0) * 100.0, 8),
    "volatility_pct": round(vol * 100.0, 8),
    "sharpe_ratio": round((avg * annualization) / vol, 8) if vol else None,
    "sortino_ratio": round((avg * annualization) / downside_dev, 8) if downside_dev else None,
    "calmar_ratio": None,
    "profit_factor": round(gross_profit / gross_loss, 8) if gross_loss else None,
    "expectancy": round(mean([float(t.get("pnl", 0.0)) for t in trades]), 8) if trades else None,
    "win_rate": round(len(wins) / len(trades), 8) if trades else None,
    "loss_rate": round(len(losses) / len(trades), 8) if trades else None,
    "average_win": round(mean(wins), 8) if wins else None,
    "average_loss": round(mean(losses), 8) if losses else None,
    "best_trade": max(wins) if wins else None,
    "worst_trade": min(losses) if losses else None,
    "drawdown": calculate_drawdown(ordered),
  }
