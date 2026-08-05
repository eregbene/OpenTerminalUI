from __future__ import annotations

from math import sqrt
from statistics import mean, pstdev

from backend.research.models import EquityPoint, MetricSet, TradeSimulation


def compute_metrics(equity_curve: list[EquityPoint], trades: list[TradeSimulation], *, periods_per_year: int = 252) -> MetricSet:
    warnings: list[str] = []
    if len(equity_curve) < 2:
        return MetricSet(total_return=0, maximum_drawdown=0, drawdown_duration=0, trade_count=len(trades), warnings=["insufficient equity samples"])
    start = equity_curve[0].equity
    end = equity_curve[-1].equity
    total_return = (end / start - 1.0) if start else 0.0
    returns = []
    for prev, cur in zip(equity_curve, equity_curve[1:]):
        if prev.equity:
            returns.append(cur.equity / prev.equity - 1.0)
    vol = pstdev(returns) * sqrt(periods_per_year) if len(returns) >= 2 else None
    ann_return = ((1 + total_return) ** (periods_per_year / max(len(returns), 1)) - 1) if returns else None
    sharpe = (mean(returns) / pstdev(returns) * sqrt(periods_per_year)) if len(returns) >= 2 and pstdev(returns) else None
    downside = [min(0.0, row) for row in returns]
    downside_dev = pstdev(downside) if len(downside) >= 2 else 0.0
    sortino = (mean(returns) / downside_dev * sqrt(periods_per_year)) if downside_dev else None
    max_dd, dd_duration = max_drawdown(equity_curve)
    calmar = (ann_return / abs(max_dd)) if ann_return is not None and max_dd < 0 else None
    wins = [trade.net_pnl for trade in trades if trade.net_pnl > 0]
    losses = [trade.net_pnl for trade in trades if trade.net_pnl < 0]
    trade_count = len(trades)
    if trade_count < 10:
        warnings.append("metric ratios are low-confidence with fewer than 10 trades")
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss else None
    if wins and not losses:
        warnings.append("profit factor is unavailable because there are no losing trades")
    avg_win = mean(wins) if wins else None
    avg_loss = mean(losses) if losses else None
    holding = [_holding_bars(trade) for trade in trades]
    return MetricSet(
        total_return=total_return,
        annualized_return=ann_return,
        annualized_volatility=vol,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        calmar_ratio=calmar,
        maximum_drawdown=max_dd,
        drawdown_duration=dd_duration,
        win_rate=len(wins) / trade_count if trade_count else None,
        loss_rate=len(losses) / trade_count if trade_count else None,
        profit_factor=profit_factor,
        expectancy=mean([trade.net_pnl for trade in trades]) if trades else None,
        average_win=avg_win,
        average_loss=avg_loss,
        payoff_ratio=(avg_win / abs(avg_loss)) if avg_win is not None and avg_loss else None,
        trade_count=trade_count,
        exposure_time=min(1.0, sum(holding) / max(len(equity_curve), 1)) if trades else 0.0,
        turnover=sum(abs(trade.entry_price * trade.quantity) for trade in trades) / max(start, 1),
        average_holding_period=mean(holding) if holding else None,
        best_trade=max([trade.net_pnl for trade in trades], default=None),
        worst_trade=min([trade.net_pnl for trade in trades], default=None),
        consecutive_wins=_max_streak([trade.net_pnl > 0 for trade in trades], True),
        consecutive_losses=_max_streak([trade.net_pnl < 0 for trade in trades], True),
        warnings=warnings,
    )


def max_drawdown(equity_curve: list[EquityPoint]) -> tuple[float, int]:
    peak = equity_curve[0].equity if equity_curve else 0.0
    max_dd = 0.0
    current_duration = 0
    max_duration = 0
    for point in equity_curve:
        if point.equity >= peak:
            peak = point.equity
            current_duration = 0
        else:
            current_duration += 1
            max_duration = max(max_duration, current_duration)
        if peak:
            max_dd = min(max_dd, point.equity / peak - 1.0)
    return max_dd, max_duration


def _holding_bars(trade: TradeSimulation) -> int:
    seconds = max((trade.exit_time - trade.entry_time).total_seconds(), 0)
    return max(1, int(seconds // 60))


def _max_streak(values: list[bool], target: bool) -> int:
    best = current = 0
    for value in values:
        if value is target:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best
