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
    instrument_id: str
    con_id: int | None = None
    quantity: Decimal
    average_cost: Decimal
    market_price: Decimal | None = None
    market_value: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    realized_pnl: Decimal | None = None
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
