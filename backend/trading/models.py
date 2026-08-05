from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


Money = Decimal
Quantity = Decimal


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class TradingBaseModel(BaseModel):
    model_config = ConfigDict(json_encoders={Decimal: str})


class PaperAccountStatus(str, Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    DISABLED = "DISABLED"
    CLOSED = "CLOSED"
    RESETTING = "RESETTING"


class DeploymentStatus(str, Enum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    ENABLED = "ENABLED"
    PAUSED = "PAUSED"
    DISABLED = "DISABLED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    ERROR = "ERROR"


class RiskDecision(str, Enum):
    APPROVED = "APPROVED"
    APPROVED_WITH_RESIZE = "APPROVED_WITH_RESIZE"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"


class OrderStatus(str, Enum):
    CREATED = "CREATED"
    PENDING_RISK = "PENDING_RISK"
    RISK_REJECTED = "RISK_REJECTED"
    APPROVED = "APPROVED"
    QUEUED = "QUEUED"
    SUBMITTED_TO_SIMULATOR = "SUBMITTED_TO_SIMULATOR"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REPLACE_PENDING = "REPLACE_PENDING"
    REPLACED = "REPLACED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    ERROR = "ERROR"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    SHORT = "SHORT"
    COVER = "COVER"


class PaperOrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"
    MARKET_ON_OPEN = "MARKET_ON_OPEN"
    MARKET_ON_CLOSE = "MARKET_ON_CLOSE"
    BRACKET = "BRACKET"
    STAGED_TARGET = "STAGED_TARGET"


class TimeInForce(str, Enum):
    DAY = "DAY"
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    GTD = "GTD"
    SESSION = "SESSION"


class EmergencyStatus(str, Enum):
    CLEAR = "CLEAR"
    ENABLED = "ENABLED"


class InstrumentReference(TradingBaseModel):
    instrument_id: str
    symbol: str
    asset_class: str = "EQUITY"
    quote_currency: str = "USD"
    minimum_tick: Decimal = Decimal("0.01")
    lot_size: Decimal = Decimal("1")
    contract_multiplier: Decimal = Decimal("1")
    shortable: bool = False
    market_open: bool = True
    halted: bool = False
    expired: bool = False
    sector: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MarketReference(TradingBaseModel):
    price: Decimal
    timestamp: datetime = Field(default_factory=utcnow)
    provider: str = "internal"
    quality_score: Decimal = Decimal("1")
    is_stale: bool = False
    is_delayed: bool = False
    is_simulated: bool = True
    is_fallback: bool = False
    bid: Decimal | None = None
    ask: Decimal | None = None
    conversion_rate: Decimal = Decimal("1")
    conversion_source: str = "base"
    conversion_timestamp: datetime = Field(default_factory=utcnow)


class PaperAccount(TradingBaseModel):
    account_id: str = Field(default_factory=lambda: new_id("acct"))
    name: str = "Paper Account"
    base_currency: str = "USD"
    status: PaperAccountStatus = PaperAccountStatus.ACTIVE
    initial_cash: Money = Decimal("100000")
    cash_balance: Money = Decimal("100000")
    reserved_cash: Money = Decimal("0")
    equity: Money = Decimal("100000")
    buying_power: Money = Decimal("100000")
    gross_exposure: Money = Decimal("0")
    net_exposure: Money = Decimal("0")
    realized_pnl: Money = Decimal("0")
    unrealized_pnl: Money = Decimal("0")
    fees: Money = Decimal("0")
    high_water_mark: Money = Decimal("100000")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    reset_at: datetime | None = None
    version: int = 1
    metadata: dict[str, Any] = Field(default_factory=dict)


class PaperAccountSnapshot(TradingBaseModel):
    snapshot_id: str = Field(default_factory=lambda: new_id("acct_snap"))
    account_id: str
    cash_balance: Money
    reserved_cash: Money
    equity: Money
    buying_power: Money
    gross_exposure: Money
    net_exposure: Money
    realized_pnl: Money
    unrealized_pnl: Money
    fees: Money
    created_at: datetime = Field(default_factory=utcnow)


class RiskPolicy(TradingBaseModel):
    policy_id: str = Field(default_factory=lambda: new_id("risk_policy"))
    name: str = "Default Paper Risk"
    version: int = 1
    immutable_after_approval: bool = True
    account: dict[str, Decimal | int | bool] = Field(
        default_factory=lambda: {
            "maximum_gross_exposure_percent": Decimal("100"),
            "maximum_net_exposure_percent": Decimal("60"),
            "maximum_long_exposure_percent": Decimal("100"),
            "maximum_short_exposure_percent": Decimal("0"),
            "maximum_open_positions": 10,
            "maximum_pending_orders": 20,
            "maximum_daily_loss_percent": Decimal("2"),
            "maximum_drawdown_percent": Decimal("10"),
            "minimum_available_cash": Decimal("0"),
            "maximum_leverage": Decimal("1"),
        }
    )
    position: dict[str, Decimal | int | bool | str] = Field(
        default_factory=lambda: {
            "maximum_position_percent": Decimal("10"),
            "maximum_risk_per_trade_percent": Decimal("1"),
            "maximum_quantity": Decimal("1000000"),
            "require_invalidation_level": True,
            "minimum_stop_distance_ticks": Decimal("2"),
            "allow_short_selling": False,
            "allow_pyramiding": False,
            "allow_averaging_down": False,
        }
    )
    instrument: dict[str, Decimal | int | bool] = Field(
        default_factory=lambda: {
            "maximum_notional": Decimal("25000"),
            "maximum_orders_per_hour": 10,
            "minimum_price": Decimal("0.01"),
        }
    )
    strategy: dict[str, Decimal | int | bool] = Field(
        default_factory=lambda: {
            "maximum_active_positions": 3,
            "maximum_daily_entries": 5,
            "maximum_losing_streak": 3,
            "maximum_total_strategy_exposure_percent": Decimal("30"),
            "one_active_order_per_proposal": True,
            "one_position_per_structural_event": True,
        }
    )
    data: dict[str, Decimal | int | bool] = Field(
        default_factory=lambda: {
            "allow_stale": False,
            "allow_simulated": True,
            "allow_fallback": False,
            "maximum_age_seconds": 60,
            "minimum_quality_score": Decimal("0.8"),
        }
    )
    created_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionModel(TradingBaseModel):
    execution_model_id: str = Field(default_factory=lambda: new_id("exec_model"))
    version: int = 1
    latency_ms: int = 0
    fill_policy: str = "NEXT_EVENT_CONSERVATIVE"
    partial_fill_policy: str = "ALL_OR_NONE_WITHOUT_VOLUME"
    spread_bps: Decimal = Decimal("2")
    slippage_bps: Decimal = Decimal("3")
    liquidity_max_quantity: Decimal | None = None
    market_closed_policy: str = "QUEUE"
    created_at: datetime = Field(default_factory=utcnow)


class StrategyDeployment(TradingBaseModel):
    deployment_id: str = Field(default_factory=lambda: new_id("deploy"))
    candidate_id: str
    strategy_id: str
    strategy_version: str
    selected_parameters: dict[str, Any] = Field(default_factory=dict)
    account_id: str
    instruments: list[InstrumentReference]
    timeframes: list[str] = Field(default_factory=lambda: ["1D"])
    schedule: dict[str, Any] = Field(default_factory=dict)
    risk_policy: RiskPolicy = Field(default_factory=RiskPolicy)
    data_policy: dict[str, Any] = Field(default_factory=dict)
    execution_model: ExecutionModel = Field(default_factory=ExecutionModel)
    status: DeploymentStatus = DeploymentStatus.DRAFT
    approved_by: str | None = None
    approved_at: datetime | None = None
    approval_notes: str | None = None
    approved_strategy_hash: str | None = None
    approved_candidate_hash: str | None = None
    expires_at: datetime | None = None
    stale: bool = False
    stale_reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    version: int = 1
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrderIntent(TradingBaseModel):
    intent_id: str = Field(default_factory=lambda: new_id("intent"))
    account_id: str
    strategy_id: str
    strategy_version: str
    deployment_id: str
    proposal_id: str
    instrument: InstrumentReference
    side: OrderSide
    order_type: PaperOrderType = PaperOrderType.MARKET
    requested_quantity: Quantity | None = None
    sizing_intent: dict[str, Any] = Field(default_factory=dict)
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    target_price: Decimal | None = None
    reference_price: Decimal
    invalidation_price: Decimal | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    correlation_id: str = Field(default_factory=lambda: new_id("corr"))
    causation_id: str | None = None
    idempotency_key: str
    structural_event_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuleEvaluation(TradingBaseModel):
    rule_id: str
    status: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    message: str


class RiskEvaluation(TradingBaseModel):
    evaluation_id: str = Field(default_factory=lambda: new_id("risk_eval"))
    account_id: str
    deployment_id: str
    proposal_id: str
    requested_direction: str
    requested_quantity: Quantity
    requested_notional: Money
    approved_quantity: Quantity = Decimal("0")
    approved_notional: Money = Decimal("0")
    decision: RiskDecision
    rules_evaluated: list[RuleEvaluation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    resizing_reasons: list[str] = Field(default_factory=list)
    account_snapshot_id: str
    market_data_reference: MarketReference
    risk_policy_id: str
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime


class PaperOrder(TradingBaseModel):
    order_id: str = Field(default_factory=lambda: new_id("order"))
    account_id: str
    strategy_id: str
    strategy_version: str
    deployment_id: str
    instrument_id: str
    asset_class: str
    side: OrderSide
    order_type: PaperOrderType
    time_in_force: TimeInForce
    quantity: Quantity
    filled_quantity: Quantity = Decimal("0")
    remaining_quantity: Quantity
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    average_fill_price: Decimal | None = None
    status: OrderStatus = OrderStatus.CREATED
    risk_evaluation_id: str
    proposal_id: str
    parent_order_id: str | None = None
    oco_group_id: str | None = None
    correlation_id: str
    causation_id: str | None = None
    idempotency_key: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    version: int = 1
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrderAmendment(TradingBaseModel):
    amendment_id: str = Field(default_factory=lambda: new_id("amend"))
    order_id: str
    new_quantity: Quantity | None = None
    new_limit_price: Decimal | None = None
    new_stop_price: Decimal | None = None
    reason: str
    created_at: datetime = Field(default_factory=utcnow)


class OrderCancellation(TradingBaseModel):
    cancellation_id: str = Field(default_factory=lambda: new_id("cancel"))
    order_id: str
    reason: str
    requested_by: str = "system"
    created_at: datetime = Field(default_factory=utcnow)


class PaperFill(TradingBaseModel):
    fill_id: str = Field(default_factory=lambda: new_id("fill"))
    order_id: str
    account_id: str
    deployment_id: str
    instrument_id: str
    side: OrderSide
    quantity: Quantity
    price: Decimal
    fees: Money
    sequence: int = 1
    execution_model_id: str
    execution_model_version: int
    liquidity_reason: str = "full_fill"
    created_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Position(TradingBaseModel):
    position_id: str = Field(default_factory=lambda: new_id("pos"))
    account_id: str
    instrument_id: str
    asset_class: str = "EQUITY"
    quantity: Quantity = Decimal("0")
    average_price: Decimal = Decimal("0")
    realized_pnl: Money = Decimal("0")
    unrealized_pnl: Money = Decimal("0")
    currency: str = "USD"
    updated_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PositionLot(TradingBaseModel):
    lot_id: str = Field(default_factory=lambda: new_id("lot"))
    position_id: str
    quantity: Quantity
    price: Decimal
    opened_at: datetime = Field(default_factory=utcnow)


class CashBalance(TradingBaseModel):
    account_id: str
    currency: str
    cash: Money
    reserved: Money = Decimal("0")
    updated_at: datetime = Field(default_factory=utcnow)


class ExposureSnapshot(TradingBaseModel):
    gross: Money
    net: Money
    long: Money
    short: Money
    by_asset_class: dict[str, str] = Field(default_factory=dict)
    by_currency: dict[str, str] = Field(default_factory=dict)


class PnLSnapshot(TradingBaseModel):
    realized: Money
    unrealized: Money
    daily_realized: Money = Decimal("0")
    daily_unrealized: Money = Decimal("0")
    drawdown_percent: Decimal = Decimal("0")


class PortfolioSnapshot(TradingBaseModel):
    snapshot_id: str = Field(default_factory=lambda: new_id("portfolio_snap"))
    account: PaperAccountSnapshot
    positions: list[Position]
    orders: list[PaperOrder]
    exposure: ExposureSnapshot
    pnl: PnLSnapshot
    marks: dict[str, MarketReference] = Field(default_factory=dict)
    risk_state: dict[str, Any] = Field(default_factory=dict)
    deployment_state: dict[str, Any] = Field(default_factory=dict)
    data_quality: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class TradingSessionState(TradingBaseModel):
    session_id: str = Field(default_factory=lambda: new_id("session"))
    account_id: str
    opened_at: datetime = Field(default_factory=utcnow)
    realized_pnl: Money = Decimal("0")
    unrealized_pnl: Money = Decimal("0")
    new_entries_blocked: bool = False
    reduce_only: bool = False


class EmergencyControl(TradingBaseModel):
    control_id: str = Field(default_factory=lambda: new_id("emergency"))
    scope: str = "account"
    account_id: str | None = None
    deployment_id: str | None = None
    status: EmergencyStatus = EmergencyStatus.CLEAR
    reason: str | None = None
    updated_by: str = "system"
    updated_at: datetime = Field(default_factory=utcnow)


class ReconciliationResult(TradingBaseModel):
    reconciliation_id: str = Field(default_factory=lambda: new_id("recon"))
    account_id: str
    status: str
    differences: list[str] = Field(default_factory=list)
    calculated_equity: Money
    stored_equity: Money
    created_at: datetime = Field(default_factory=utcnow)


class AuditRecord(TradingBaseModel):
    audit_id: str = Field(default_factory=lambda: new_id("audit"))
    event_type: str
    entity_type: str
    entity_id: str | None = None
    account_id: str | None = None
    deployment_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class LedgerEntry(TradingBaseModel):
    ledger_id: str = Field(default_factory=lambda: new_id("ledger"))
    account_id: str
    event_type: str
    instrument_id: str | None = None
    cash_delta: Money = Decimal("0")
    reserved_cash_delta: Money = Decimal("0")
    quantity_delta: Quantity = Decimal("0")
    price: Decimal | None = None
    fees: Money = Decimal("0")
    realized_pnl: Money = Decimal("0")
    currency: str = "USD"
    causation_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SizingDecision(TradingBaseModel):
    requested_quantity: Quantity
    approved_quantity: Quantity
    requested_notional: Money
    approved_notional: Money
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

