from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def broker_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class BrokerModel(BaseModel):
    model_config = ConfigDict(json_encoders={Decimal: str})


class BrokerEnvironment(str, Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"
    SIMULATED = "SIMULATED"
    UNVERIFIED = "UNVERIFIED"


class BrokerConnectionState(str, Enum):
    DISABLED = "DISABLED"
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    RECONNECTING = "RECONNECTING"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class BrokerHealthState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"
    DISCONNECTED = "DISCONNECTED"
    DISABLED = "DISABLED"


class MarketDataMode(str, Enum):
    REALTIME = "REALTIME"
    FROZEN = "FROZEN"
    DELAYED = "DELAYED"
    DELAYED_FROZEN = "DELAYED_FROZEN"
    UNAVAILABLE = "UNAVAILABLE"
    SUBSCRIPTION_REQUIRED = "SUBSCRIPTION_REQUIRED"


class DataQuality(str, Enum):
    VALID = "VALID"
    DELAYED = "DELAYED"
    PARTIAL = "PARTIAL"
    STALE = "STALE"
    GAPPED = "GAPPED"
    INVALID = "INVALID"
    UNAVAILABLE = "UNAVAILABLE"


class BrokerOrderState(str, Enum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    SUBMITTING = "SUBMITTING"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    SUBMITTED = "SUBMITTED"
    PRESUBMITTED = "PRESUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    INACTIVE = "INACTIVE"
    EXPIRED = "EXPIRED"
    ERROR = "ERROR"


class BrokerContract(BrokerModel):
    instrument_id: str
    symbol: str
    asset_type: str
    exchange: str
    currency: str
    security_type: str
    primary_exchange: str | None = None
    local_symbol: str | None = None
    trading_class: str | None = None
    expiry: str | None = None
    multiplier: str | None = None
    con_id: int
    resolved_at: datetime = Field(default_factory=now_utc)
    source: str = "ibkr"
    resolution_version: str = "phase11.v1"


class BrokerQuote(BrokerModel):
    instrument_id: str
    bid: Decimal | None = None
    ask: Decimal | None = None
    last: Decimal | None = None
    midpoint: Decimal | None = None
    volume: Decimal | None = None
    timestamp: datetime = Field(default_factory=now_utc)
    mode: MarketDataMode = MarketDataMode.DELAYED
    quality: DataQuality = DataQuality.DELAYED


class BrokerBar(BrokerModel):
    instrument_id: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal("0")
    completed: bool = True
    mode: MarketDataMode = MarketDataMode.DELAYED
    quality: DataQuality = DataQuality.DELAYED


class BrokerCashBalance(BrokerModel):
    currency: str
    settled_cash: Decimal
    available_cash: Decimal
    conversion_rate: Decimal | None = None
    rate_timestamp: datetime | None = None
    source: str = "broker"


class BrokerPosition(BrokerModel):
    # Broker Independence Assessment Phase 1: canonical_position_id is REQUIRED and must be
    # constructed deterministically by each adapter (e.g. f"{broker}:{account_id}:
    # {broker_position_id}") -- same real position queried twice must always yield the same
    # canonical_position_id, unlike e.g. BrokerAccountSnapshot.snapshot_id, which is legitimately
    # random-per-snapshot. broker_position_id carries the raw native ticket/positionId for the
    # adapter's own internal use building modify/close requests -- never read above the adapter
    # layer. account_id/broker are what make position-level account-aware routing possible once
    # multiple accounts/brokers are active simultaneously (see BrokerAccountSnapshot's existing
    # account_id/broker fields for the established pattern this mirrors). stop_loss/take_profit
    # are new -- absent before this phase; None means "no stop/target currently set on this
    # position", not "leave unchanged" (that distinction only matters for the modify COMMAND
    # below, never for a position snapshot).
    canonical_position_id: str
    broker_position_id: str | None = None
    account_id: str
    broker: str = "mt5"
    instrument_id: str
    con_id: int | None = None
    quantity: Decimal
    average_cost: Decimal
    market_price: Decimal | None = None
    market_value: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    realized_pnl: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    currency: str = "USD"
    timestamp: datetime = Field(default_factory=now_utc)
    unresolved: bool = False


class BrokerAccount(BrokerModel):
    account_id: str
    alias: str
    broker: str = "ibkr"
    environment: BrokerEnvironment
    base_currency: str = "USD"
    paper_verified: bool = False
    allowed: bool = False
    account_type: str = "PAPER"


class BrokerAccountSnapshot(BrokerModel):
    snapshot_id: str = Field(default_factory=lambda: broker_id("broker_snap"))
    account_id: str
    broker: str = "ibkr"
    environment: BrokerEnvironment
    broker_timestamp: datetime = Field(default_factory=now_utc)
    received_timestamp: datetime = Field(default_factory=now_utc)
    freshness: str = "fresh"
    sequence: int = 1
    net_liquidation: Decimal = Decimal("100000")
    buying_power: Decimal = Decimal("100000")
    available_funds: Decimal = Decimal("100000")
    excess_liquidity: Decimal = Decimal("100000")
    initial_margin: Decimal = Decimal("0")
    maintenance_margin: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    cash: list[BrokerCashBalance] = Field(default_factory=list)
    positions: list[BrokerPosition] = Field(default_factory=list)
    content_hash: str | None = None


class BrokerOrder(BrokerModel):
    canonical_order_id: str
    broker_order_id: str | None = None
    permanent_id: str | None = None
    account_id: str
    broker: str = "mt5"
    instrument_id: str
    state: BrokerOrderState
    raw_status: str | None = None
    submitted_at: datetime | None = None
    average_price: Decimal | None = None
    filled_quantity: Decimal = Decimal("0")
    remaining_quantity: Decimal = Decimal("0")
    rejection_reason: str | None = None
    correlation_id: str | None = None


class BrokerExecution(BrokerModel):
    execution_id: str = Field(default_factory=lambda: broker_id("exec"))
    canonical_order_id: str
    broker_order_id: str | None = None
    account_id: str
    broker: str = "mt5"
    instrument_id: str
    side: str
    quantity: Decimal
    price: Decimal
    commission: Decimal = Decimal("0")
    currency: str = "USD"
    timestamp: datetime = Field(default_factory=now_utc)
    sequence: int = 1


class BrokerOrderCommand(BrokerModel):
    canonical_order_id: str
    account_id: str
    instrument_id: str
    side: str
    order_type: str
    time_in_force: str
    quantity: Decimal
    approved_quantity: Decimal
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    risk_evaluation_id: str
    idempotency_key: str
    correlation_id: str | None = None
    user_approval: bool = True


class BrokerOrderReceipt(BrokerModel):
    order: BrokerOrder
    execution: BrokerExecution | None = None
    submission_state: str = "ACKNOWLEDGED"


class BrokerCancelCommand(BrokerModel):
    canonical_order_id: str
    broker_order_id: str
    account_id: str
    reason: str


class BrokerCancelReceipt(BrokerModel):
    canonical_order_id: str
    broker_order_id: str
    state: BrokerOrderState


class BrokerModifyPositionCommand(BrokerModel):
    """Broker Independence Assessment Phase 1, item 2: SL/TP modification as a first-class
    operation -- previously absent from BrokerOrderAdapter entirely (every SL/TP change was
    hand-built as a raw MT5 order_send() request dict inside adaptive_management/service.py).
    stop_loss/take_profit each independently None-able: None means "leave this one exactly as it
    currently is", NOT "clear it" -- clearing a stop/target intentionally means passing
    Decimal("0") (mirroring MT5's own order_send semantics, where omitting sl/tp from the request
    leaves the existing value alone but an explicit 0 clears it). Each adapter is responsible for
    translating this distinction correctly into its own native modify/amend request."""
    canonical_position_id: str
    broker_position_id: str
    account_id: str
    instrument_id: str
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    idempotency_key: str
    correlation_id: str | None = None


class BrokerModifyReceipt(BrokerModel):
    position: BrokerPosition
    submission_state: str = "ACKNOWLEDGED"


class BrokerClosePositionCommand(BrokerModel):
    """quantity=None means full close; otherwise a partial close of exactly that quantity (in
    canonical lots -- see BrokerSymbolSpec below for why this must never be a broker-native
    volume unit at this layer)."""
    canonical_position_id: str
    broker_position_id: str
    account_id: str
    instrument_id: str
    quantity: Decimal | None = None
    idempotency_key: str
    correlation_id: str | None = None


class BrokerCloseReceipt(BrokerModel):
    canonical_position_id: str
    broker_position_id: str
    closed_quantity: Decimal
    remaining_quantity: Decimal
    execution: BrokerExecution | None = None
    remaining_position: BrokerPosition | None = None
    submission_state: str = "ACKNOWLEDGED"


class BrokerSymbolSpec(BrokerModel):
    """Broker Independence Assessment Phase 1, item 3: FX-specific instrument metadata, kept
    deliberately SEPARATE from BrokerContract (which is identity/exchange-shaped -- con_id,
    security_type, primary_exchange -- carried over from the original IBKR/equities design and
    largely meaningless for FX). BrokerContract answers "what instrument is this"; this model
    answers "how do I convert between canonical lots and this broker's native volume unit, and
    between price movement and money" for that instrument.

    CRITICAL DESIGN RULE (user-specified): every field below is expressed so that strategy/risk/
    portfolio code NEVER needs to know a broker's native volume representation. `lot_size` is
    "how many base-currency units make up 1.0 canonical lot for this symbol on this broker" --
    the ONE number an adapter needs to convert canonical lots to its own native volume field
    (MT5: lots directly, volume_step-bounded; cTrader: centilots, i.e. roughly
    lots * lot_size * 100, but read from the symbol's own live lotSize, never a hardcoded
    constant -- see the assessment's own Section 6). volume_min/max/step are expressed in
    CANONICAL LOTS, already converted by the adapter from whatever native unit the broker
    reports them in -- code above the adapter layer only ever reasons in lots."""
    instrument_id: str
    broker: str = "mt5"
    pip_position: int
    pip_size: Decimal
    digits: int
    lot_size: Decimal
    volume_min: Decimal
    volume_max: Decimal
    volume_step: Decimal
    contract_size: Decimal
    margin_currency: str = "USD"
    stops_level: Decimal | None = None
    freeze_level: Decimal | None = None
    resolved_at: datetime = Field(default_factory=now_utc)


class ReconciliationDifference(BrokerModel):
    category: str
    entity_id: str
    detail: str


class BrokerReconciliationResult(BrokerModel):
    reconciliation_id: str = Field(default_factory=lambda: broker_id("broker_recon"))
    account_id: str
    status: str
    differences: list[ReconciliationDifference] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now_utc)
