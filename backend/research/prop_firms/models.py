from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


PhaseState = Literal[
    "NOT_STARTED",
    "ACTIVE",
    "DAILY_LOCKED",
    "WEEKLY_LOCKED",
    "PROFIT_TARGET_REACHED",
    "WAITING_FOR_MINIMUM_DAYS",
    "PHASE_PASSED",
    "PHASE_FAILED",
    "TOTAL_DRAWDOWN_BREACH",
    "DAILY_DRAWDOWN_BREACH",
    "CONSISTENCY_NOT_MET",
    "INACTIVITY_BREACH",
    "NEWS_RULE_BREACH",
    "PROGRAM_RULE_BREACH",
]


@dataclass(frozen=True)
class PropFirmProfile:
    profile_id: str
    provider_name: str
    program_name: str
    version: str
    verified_at: str | None
    source_reference: str
    account_size: float
    account_currency: str
    phase: str
    profit_target_percent: float
    maximum_daily_loss_percent: float
    maximum_total_loss_percent: float
    daily_loss_calculation_method: str
    total_loss_calculation_method: str
    static_or_trailing_drawdown: str
    minimum_trading_days: int
    minimum_profitable_days: int
    profitable_day_threshold_percent: float
    maximum_trading_days: int | None
    trading_period_unlimited: bool
    best_day_consistency_percent: float | None
    general_consistency_rule: str
    inactivity_days: int | None
    overnight_holding_allowed: bool
    weekend_holding_allowed: bool
    news_holding_allowed: bool
    news_entry_blackout_before_minutes: int
    news_entry_blackout_after_minutes: int
    automated_trading_allowed: str
    maximum_leverage: float | None
    allowed_symbols: list[str]
    prohibited_strategies: list[str]
    daily_reset_timezone: str
    phase_transition_rules: dict[str, Any]
    funded_account_rules: dict[str, Any]
    payout_consistency_rules: dict[str, Any]
    notes: str

    def model_dump(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class InternalSafetyProfile:
    risk_per_trade_percent: float = 0.25
    max_daily_loss_percent: float = 1.0
    max_weekly_loss_percent: float = 2.0
    max_total_drawdown_percent: float = 4.0
    max_open_positions: int = 1
    max_trades_per_day: int = 2
    max_consecutive_losses: int = 2
    daily_profit_lock_percent: float = 1.0
    min_risk_reward: float = 1.5
    max_correlated_exposure_percent: float = 0.5
    drawdown_buffer_percent: float = 1.0

    def model_dump(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class ChallengeAccountState:
    initial_balance: float
    current_balance: float
    current_equity: float
    intraday_high_equity: float
    previous_day_balance: float
    previous_day_equity: float
    daily_closed_pnl: float = 0.0
    floating_pnl: float = 0.0
    commissions: float = 0.0
    swaps: float = 0.0
    slippage: float = 0.0
    daily_loss_allowance: float = 0.0
    total_loss_allowance: float = 0.0
    profit_target: float = 0.0
    trading_days: int = 0
    profitable_days: int = 0
    best_trading_day: float = 0.0
    consecutive_losses: int = 0
    weekly_pnl: float = 0.0
    phase_state: PhaseState = "NOT_STARTED"
    breach_reason: str | None = None
    phase_completion_timestamp: str | None = None
    day_pnls: dict[str, float] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class ExecutionScenario:
    name: str
    spread_pips: float
    commission_per_lot: float
    slippage_pips: float
    stop_slippage_pips: float
    swap_per_day: float
    rejected_entry_probability: float
    partial_fill_probability: float
    fill_delay_bars: int

    def model_dump(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class NewsEvent:
    timestamp: str
    currency: str
    impact: str
    event_name: str
    blackout_before_minutes: int
    blackout_after_minutes: int
    source: str


@dataclass(frozen=True)
class PropFirmSimulationRequest:
    profile_ids: list[str]
    symbol: str = "EURUSD"
    account_size: float = 100000.0
    risk_per_trade_percents: list[float] = field(default_factory=lambda: [0.10, 0.20, 0.25, 0.30, 0.40, 0.50])
    trade_frequency_caps: list[int] = field(default_factory=lambda: [1, 2, 3, 5])
    execution_scenarios: list[str] = field(default_factory=lambda: ["NORMAL", "STRESSED"])
    days: int = 90
    policy: str = "accept_first_valid_candidate_of_day_v1"
    monte_carlo_runs: int = 1000
    seed: int = 42


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
