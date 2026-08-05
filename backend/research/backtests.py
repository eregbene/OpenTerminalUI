from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.market_data.models import AssetClass
from backend.research.lineage import build_lineage
from backend.research.metrics import compute_metrics
from backend.research.models import (
    BacktestConfiguration,
    BacktestResult,
    BacktestRun,
    DatasetSnapshot,
    EquityPoint,
    FillPolicy,
    ResearchStatus,
    SimulatedOrder,
    SimulatedOrderType,
    TradeSimulation,
    stable_id,
    utc_now,
)
from backend.strategies.engine import StrategyEngine
from backend.strategies.models import DecisionType, StrategySpec


class EventDrivenBacktester:
    def __init__(self, engine: StrategyEngine | None = None) -> None:
        self.engine = engine or StrategyEngine()

    def run(
        self,
        spec: StrategySpec,
        dataset: DatasetSnapshot,
        config: BacktestConfiguration,
        *,
        parameter_set: dict[str, Any] | None = None,
        random_seed: int = 42,
    ) -> BacktestRun:
        if len(dataset.bars) > config.max_bars:
            raise ValueError("bar count exceeds backtest configuration limit")
        parameter_set = parameter_set or {}
        lineage = build_lineage(spec, dataset, config, parameter_set, random_seed=random_seed)
        run = BacktestRun(
            run_id=stable_id("bt", lineage.correlation_id, utc_now()),
            status=ResearchStatus.RUNNING,
            started_at=utc_now(),
            lineage=lineage,
            configuration=config,
        )
        cash = config.initial_capital
        position: dict[str, Any] | None = None
        orders: list[SimulatedOrder] = []
        trades: list[TradeSimulation] = []
        equity: list[EquityPoint] = []
        warnings: list[str] = []
        bars = dataset.bars
        for idx, bar in enumerate(bars):
            ts = _timestamp(bar)
            if position is not None:
                exit_price, reason, ambiguous = _exit_price(position, bar, config.execution_model.fill_policy)
                if exit_price is not None:
                    qty = float(position["quantity"])
                    side_mult = 1 if position["side"] == "long" else -1
                    gross = (exit_price - float(position["entry_price"])) * qty * side_mult
                    fees = config.cost_model.estimate(exit_price, qty)
                    net = gross - fees
                    cash += net
                    trades.append(
                        TradeSimulation(
                            trade_id=stable_id("trade", position["proposal_id"], ts, exit_price),
                            decision_id=position["decision_id"],
                            proposal_id=position["proposal_id"],
                            side=position["side"],
                            entry_time=position["entry_time"],
                            entry_price=position["entry_price"],
                            exit_time=ts,
                            exit_price=exit_price,
                            quantity=qty,
                            gross_pnl=gross,
                            fees=fees + float(position["entry_fees"]),
                            net_pnl=net - float(position["entry_fees"]),
                            return_pct=(exit_price / float(position["entry_price"]) - 1) * side_mult,
                            exit_reason=reason,
                            ambiguous_fill=ambiguous,
                            evidence_reference=position["decision_id"],
                        )
                    )
                    if ambiguous:
                        warnings.append(f"ambiguous intrabar fill resolved by {config.execution_model.fill_policy}")
                    position = None
            eligible = [order for order in orders if order.status == "submitted" and order.eligible_from <= ts]
            if position is None and eligible:
                order = eligible[0]
                fill_price = _entry_price(order, bar)
                if fill_price is not None:
                    order.status = "filled"
                    order.filled_quantity = order.quantity
                    order.average_fill_price = fill_price
                    entry_fees = config.cost_model.estimate(fill_price, order.quantity)
                    cash -= entry_fees
                    position = {
                        "side": "long" if order.side == "buy" else "short",
                        "quantity": order.quantity,
                        "entry_time": ts,
                        "entry_price": fill_price,
                        "entry_fees": entry_fees,
                        "proposal_id": order.proposal_id,
                        "decision_id": order.decision_id or order.proposal_id,
                        "stop": order.invalidation_price,
                        "target": order.target_price,
                    }
            if idx >= max(spec.required_history, 1) and idx < len(bars) - config.execution_model.entry_delay_bars:
                evaluation = self.engine.evaluate_bars(spec, bars[: idx + 1], symbol=dataset.symbol, asset_class=AssetClass.EQUITY, dataset_snapshot_id=dataset.dataset_snapshot_id)
                decision = evaluation.decisions[0]
                if decision.decision_type in {DecisionType.LONG, DecisionType.SHORT, "long", "short"} and decision.proposal is not None and position is None:
                    eligible_bar = bars[idx + config.execution_model.entry_delay_bars]
                    order = SimulatedOrder(
                        simulated_order_id=stable_id("ord", decision.proposal.proposal_id),
                        proposal_id=decision.proposal.proposal_id,
                        type=config.execution_model.default_order_type,
                        side="buy" if str(decision.direction) == "long" else "sell",
                        quantity=config.quantity,
                        submitted_at=ts,
                        eligible_from=_timestamp(eligible_bar),
                        decision_id=decision.decision_id,
                        invalidation_price=decision.proposal.invalidation_price,
                        target_price=decision.proposal.target_levels[0].price if decision.proposal.target_levels else None,
                    )
                    orders.append(order)
            mark = float(bar["close"])
            unrealized = 0.0
            exposure = 0.0
            if position is not None:
                side_mult = 1 if position["side"] == "long" else -1
                unrealized = (mark - float(position["entry_price"])) * float(position["quantity"]) * side_mult
                exposure = abs(mark * float(position["quantity"]))
            equity.append(EquityPoint(timestamp=ts, equity=cash + unrealized, cash=cash, unrealized_pnl=unrealized, exposure=exposure))
        metrics = compute_metrics(equity, trades)
        run.status = ResearchStatus.COMPLETED
        run.completed_at = utc_now()
        run.result = BacktestResult(metrics=metrics, trades=trades, equity_curve=equity, orders=orders, warnings=[*warnings, *metrics.warnings], attribution=_attribution(trades))
        return run


def _entry_price(order: SimulatedOrder, bar: dict[str, Any]) -> float | None:
    if order.type in {SimulatedOrderType.MARKET, SimulatedOrderType.MARKET_ON_NEXT_BAR, "market", "market_on_next_bar"}:
        return float(bar["open"])
    return order.limit_price


def _exit_price(position: dict[str, Any], bar: dict[str, Any], policy: FillPolicy) -> tuple[float | None, str, bool]:
    stop = position.get("stop")
    target = position.get("target")
    high = float(bar["high"])
    low = float(bar["low"])
    side = position["side"]
    stop_hit = stop is not None and ((side == "long" and low <= stop) or (side == "short" and high >= stop))
    target_hit = target is not None and ((side == "long" and high >= target) or (side == "short" and low <= target))
    if stop_hit and target_hit:
        if policy in {FillPolicy.NO_FILL_ON_AMBIGUITY, FillPolicy.LOWER_TIMEFRAME_REQUIRED, "no_fill_on_ambiguity", "lower_timeframe_required"}:
            return None, "ambiguous_no_fill", True
        if policy in {FillPolicy.OPTIMISTIC, FillPolicy.TARGET_FIRST, "optimistic", "target_first"}:
            return float(target), "target", True
        return float(stop), "stop", True
    if stop_hit:
        return float(stop), "stop", False
    if target_hit:
        return float(target), "target", False
    return None, "", False


def _timestamp(bar: dict[str, Any]) -> datetime:
    value = bar.get("timestamp") or bar.get("close_time") or bar.get("time")
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _attribution(trades: list[TradeSimulation]) -> dict[str, Any]:
    long_pnl = sum(t.net_pnl for t in trades if t.side == "long")
    short_pnl = sum(t.net_pnl for t in trades if t.side == "short")
    by_exit: dict[str, float] = {}
    for trade in trades:
        by_exit[trade.exit_reason] = by_exit.get(trade.exit_reason, 0.0) + trade.net_pnl
    return {"long_pnl": long_pnl, "short_pnl": short_pnl, "by_exit_reason": by_exit}
