from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MT5Model(BaseModel):
    model_config = ConfigDict(json_encoders={Decimal: str})


class MT5TerminalStatus(MT5Model):
    connected: bool
    initialized: bool
    package_available: bool
    terminal_info: dict[str, Any] = Field(default_factory=dict)
    version: tuple[int, int, str] | None = None
    last_error: tuple[int, str] | None = None
    terminal_installed: bool = False
    terminal_running: bool = False
    initialize_success: bool = False
    login_success: bool = False
    trade_connection_available: bool = False
    masked_login: str | None = None
    server: str | None = None
    company: str | None = None
    currency: str | None = None
    balance: Decimal | None = None
    equity: Decimal | None = None
    margin: Decimal | None = None
    free_margin: Decimal | None = None
    leverage: int | None = None
    account_trade_mode: int | None = None
    account_mode: str | None = None
    margin_mode: int | None = None
    hedging_mode: str | None = None
    trade_allowed: bool | None = None
    ea_trading_allowed: bool | None = None
    external_python_trading_allowed: bool | None = None
    last_successful_heartbeat: datetime | None = None


class MT5Account(MT5Model):
    login: int
    server: str | None = None
    currency: str | None = None
    balance: Decimal
    equity: Decimal
    margin: Decimal
    free_margin: Decimal
    leverage: int | None = None
    name: str | None = None
    company: str | None = None
    trade_mode: int | None = None


class MT5Symbol(MT5Model):
    symbol: str
    visible: bool
    selected: bool
    bid: Decimal | None = None
    ask: Decimal | None = None
    spread: int | None = None
    digits: int | None = None
    trade_mode: int | None = None
    currency_base: str | None = None
    currency_profit: str | None = None
    currency_margin: str | None = None
    path: str | None = None
    description: str | None = None
    trade_calc_mode: int | None = None
    point: Decimal | None = None
    trade_tick_size: Decimal | None = None
    trade_tick_value: Decimal | None = None
    trade_tick_value_profit: Decimal | None = None
    trade_tick_value_loss: Decimal | None = None
    trade_contract_size: Decimal | None = None
    volume_min: Decimal | None = None
    volume_max: Decimal | None = None
    volume_step: Decimal | None = None
    trade_stops_level: int | None = None
    trade_freeze_level: int | None = None
    spread_float: bool | None = None
    swap_long: Decimal | None = None
    swap_short: Decimal | None = None
    swap_mode: int | None = None
    margin_initial: Decimal | None = None
    margin_maintenance: Decimal | None = None
    filling_mode: int | None = None
    order_mode: int | None = None
    trade_execution: int | None = None


class MT5Quote(MT5Model):
    symbol: str
    bid: Decimal | None = None
    ask: Decimal | None = None
    last: Decimal | None = None
    spread: Decimal | None = None
    time: datetime | None = None


class MT5Candle(MT5Model):
    symbol: str
    timeframe: str
    time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    tick_volume: int
    spread: int
    real_volume: int
    close_time: datetime | None = None
    complete: bool = True
    source: str = "MT5"
    server: str | None = None
    ingestion_timestamp: datetime | None = None
    quality_flags: list[str] = Field(default_factory=list)


class MT5Position(MT5Model):
    ticket: int
    symbol: str
    type: int
    volume: Decimal
    price_open: Decimal
    price_current: Decimal | None = None
    sl: Decimal | None = None
    tp: Decimal | None = None
    profit: Decimal | None = None
    swap: Decimal | None = None
    commission: Decimal | None = None
    magic: int | None = None
    comment: str | None = None
    identifier: int | None = None
    time: datetime | None = None


class MT5Order(MT5Model):
    ticket: int
    symbol: str
    type: int
    volume_current: Decimal
    price_open: Decimal | None = None
    sl: Decimal | None = None
    tp: Decimal | None = None
    magic: int | None = None
    comment: str | None = None
    state: int | None = None
    time_setup: datetime | None = None


class MT5HistoryItem(MT5Model):
    ticket: int
    order: int | None = None
    symbol: str | None = None
    type: int | None = None
    volume: Decimal | None = None
    price: Decimal | None = None
    profit: Decimal | None = None
    commission: Decimal | None = None
    time: datetime | None = None


class MT5ForexInstrument(MT5Model):
    canonical_pair: str
    broker_symbol: str
    asset_class: str
    base_currency: str
    quote_currency: str
    enabled: bool
    visible: bool
    tradable: bool
    market_open: bool
    selected: bool
    eligible: bool
    ineligibility_reasons: list[str] = Field(default_factory=list)
    specification_timestamp: datetime
    broker_server: str | None = None
    symbol: MT5Symbol


class MT5ForexUniverse(MT5Model):
    total_symbols: int
    total_forex_pairs: int
    majors: int
    minors: int
    exotics: int
    broker_specific: int = 0
    disabled_or_unavailable: int = 0
    unknown: int = 0
    naming_patterns: list[str] = Field(default_factory=list)
    canonical_mappings: dict[str, str] = Field(default_factory=dict)
    items: list[MT5ForexInstrument] = Field(default_factory=list)


class MT5SchedulerStatus(MT5Model):
    scheduler_running: bool = False
    scheduler_owner: str = "backend-ai-trading-scheduler"
    current_state: str = "external"
    timeframe: str = "M15"
    max_ai_candidates_per_cycle: int = 1
    provider_calls_today: int = 0
    estimated_cost_today: Decimal = Decimal("0")
    order_submission_enabled: bool = False
    live_trading_enabled: bool = False
    note: str = "MT5 adapter exposes status only; existing AI scheduler remains unchanged in MT5-1."


class MT5TradeIntent(MT5Model):
    intent_id: str
    account_id: str
    broker_symbol: str
    canonical_pair: str
    direction: str
    volume: Decimal
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    deviation: int = 20
    magic: int = 5601001
    comment: str
    context_hash: str


class MT5RiskSizing(MT5Model):
    status: str
    volume: Decimal = Decimal("0")
    effective_risk_usd: Decimal = Decimal("0")
    projected_loss_usd: Decimal = Decimal("0")
    projected_profit_usd: Decimal = Decimal("0")
    risk_reward: Decimal = Decimal("0")
    reasons: list[str] = Field(default_factory=list)
    equity_risk_cap_usd: Decimal = Decimal("0")
    trade_risk_cap_usd: Decimal = Decimal("0")
    risk_multiplier: Decimal = Decimal("1.0")
    # Canonical-risk-calculator diagnostics (backend/brokers/mt5/risk_calculator.py) -- populated
    # whenever the calculator ran, regardless of APPROVED/REJECTED outcome, so a caller/API can
    # always show which method was trusted and how much the estimates disagreed.
    risk_calculation_method: str | None = None
    risk_calculation_estimates: dict[str, Any] = Field(default_factory=dict)
    risk_calculation_disagreement_pct: float | None = None
    risk_calculation_warning_codes: list[str] = Field(default_factory=list)


class MT5OrderCheckResult(MT5Model):
    ok: bool
    retcode: int | None = None
    comment: str | None = None
    margin: Decimal | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class MT5OrderSubmissionResult(MT5Model):
    status: str
    retcode: int | None = None
    comment: str | None = None
    order_ticket: int | None = None
    deal_ticket: int | None = None
    fill_price: Decimal | None = None
    requested_volume: Decimal
    filled_volume: Decimal | None = None
    request: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)
