from __future__ import annotations

from backend.forex_strategies.models import ExecutionMode, StrategyDefinition, StrategyStatus


ALL_FX = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"]
FIRST_ENABLED = ["EURUSD", "GBPUSD", "USDJPY"]


class ForexStrategyRegistry:
    def __init__(self) -> None:
        self._items = {item.strategy_id: item for item in _definitions()}

    def all(self) -> list[StrategyDefinition]:
        return list(self._items.values())

    def get(self, strategy_id: str) -> StrategyDefinition | None:
        return self._items.get(strategy_id)

    def require(self, strategy_id: str) -> StrategyDefinition:
        item = self.get(strategy_id)
        if item is None:
            raise KeyError(strategy_id)
        return item

    def set_execution_mode(self, strategy_id: str, mode: ExecutionMode) -> StrategyDefinition:
        item = self.require(strategy_id)
        if mode == ExecutionMode.RULE_BASED_AUTO_PAPER and item.status not in {StrategyStatus.PAPER_APPROVED, StrategyStatus.PAPER_ACTIVE}:
            raise ValueError("AUTO_PAPER_REQUIRES_APPROVED_STRATEGY")
        item.execution_mode = mode
        return item

    def pause(self, strategy_id: str) -> StrategyDefinition:
        item = self.require(strategy_id)
        item.enabled = False
        item.status = StrategyStatus.PAUSED
        item.execution_mode = ExecutionMode.DISABLED
        return item

    def resume(self, strategy_id: str) -> StrategyDefinition:
        item = self.require(strategy_id)
        if item.validation_scorecard_id is None:
            item.status = StrategyStatus.PAPER_CANDIDATE
            item.enabled = False
        else:
            item.status = StrategyStatus.PAPER_APPROVED
            item.enabled = True
        return item


def _base(strategy_id: str, name: str, frameworks: list[str], symbols: list[str], regimes: list[str], unsupported: list[str]) -> StrategyDefinition:
    paper_ready = strategy_id in {"trend_pullback_v1", "breakout_retest_v1"}
    return StrategyDefinition(
        strategy_id=strategy_id,
        strategy_name=name,
        status=StrategyStatus.PAPER_CANDIDATE if paper_ready else StrategyStatus.RESEARCH,
        framework_dependencies=frameworks,
        feature_dependencies=["trend_score", "momentum_score", "volatility_state", "structure_trend", "confluence_score"],
        supported_symbols=symbols,
        supported_timeframes=["15m", "1h", "4h"],
        supported_regimes=regimes,
        unsupported_regimes=unsupported,
        entry_rules=["completed_candle", "framework_alignment", "minimum_risk_reward", "fresh_market_data"],
        exit_rules=["target_reached", "stop_hit", "maximum_holding_period"],
        stop_rules=["structure_invalidation", "stop_required", "wrong_side_rejected"],
        target_rules=["fixed_2r_primary_target"],
        position_sizing_policy={"mode": "fixed_fractional_risk", "default_risk_percent": 0.25, "maximum_risk_percent": 0.50},
        maximum_holding_period="2 completed candles",
        minimum_data_quality=0.8,
        validation_scorecard_id="fx4-fixture-scorecard" if paper_ready else None,
        paper_deployment_id=f"fx4-{strategy_id}-manual" if paper_ready else None,
        execution_mode=ExecutionMode.MANUAL_CONFIRMATION,
        enabled=paper_ready,
    )


def _definitions() -> list[StrategyDefinition]:
    return [
        _base("trend_pullback_v1", "Trend Pullback", ["trend_following", "price_action", "support_resistance"], ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD"], ["trend"], ["range", "breakout_expansion"]),
        _base("breakout_retest_v1", "Breakout and Retest", ["breakout", "momentum", "support_resistance"], ALL_FX, ["breakout", "trend"], ["range_chop"]),
        _base("mean_reversion_range_v1", "Mean-Reversion Range", ["mean_reversion", "support_resistance"], ALL_FX, ["range"], ["trend", "breakout_expansion"]),
        _base("liquidity_sweep_reversal_v1", "Liquidity Sweep Reversal", ["smc", "ict", "price_action"], ALL_FX, ["range", "transition"], ["strong_trend"]),
        _base("session_breakout_v1", "Session Breakout", ["breakout", "momentum"], ALL_FX, ["breakout", "trend"], ["range_chop"]),
        StrategyDefinition(
            strategy_id="xauusd_analysis_only_v1",
            strategy_name="XAU/USD Analysis Only Guard",
            status=StrategyStatus.ANALYSIS_ONLY,
            framework_dependencies=["trend_following", "smc", "ict"],
            feature_dependencies=["confluence_score"],
            supported_symbols=["XAUUSD"],
            supported_timeframes=["15m", "1h", "4h"],
            supported_regimes=["trend", "range", "breakout"],
            entry_rules=["analysis_only"],
            exit_rules=[],
            stop_rules=[],
            target_rules=[],
            position_sizing_policy={"mode": "disabled"},
            enabled=False,
            execution_mode=ExecutionMode.DISABLED,
        ),
    ]


strategy_registry = ForexStrategyRegistry()
