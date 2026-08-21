from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from backend.brokers.capabilities import BrokerCapabilities
from backend.brokers.health import BrokerHealth
from backend.brokers.models import (
    BrokerAccount,
    BrokerAccountSnapshot,
    BrokerBar,
    BrokerCancelCommand,
    BrokerCancelReceipt,
    BrokerClosePositionCommand,
    BrokerCloseReceipt,
    BrokerContract,
    BrokerExecution,
    BrokerModifyPositionCommand,
    BrokerModifyReceipt,
    BrokerOrder,
    BrokerOrderCommand,
    BrokerOrderReceipt,
    BrokerPosition,
    BrokerQuote,
    BrokerReconciliationResult,
    BrokerSymbolSpec,
)


@runtime_checkable
class BrokerReadAdapter(Protocol):
    name: str
    capabilities: BrokerCapabilities

    async def health(self) -> BrokerHealth: ...
    async def connect(self) -> BrokerHealth: ...
    async def disconnect(self) -> BrokerHealth: ...
    async def reconnect(self) -> BrokerHealth: ...
    async def accounts(self) -> list[BrokerAccount]: ...
    async def account_snapshot(self, account_id: str) -> BrokerAccountSnapshot: ...
    async def positions(self, account_id: str) -> list[BrokerPosition]: ...
    async def open_orders(self, account_id: str) -> list[BrokerOrder]: ...
    async def executions(self, account_id: str, since: datetime | None = None) -> list[BrokerExecution]: ...
    async def resolve_contract(self, instrument_id: str) -> BrokerContract: ...
    # Broker Independence Assessment Phase 1, item 3: FX-specific instrument metadata (pip/lot/
    # volume-step/contract-size), deliberately separate from resolve_contract()'s identity-shaped
    # BrokerContract -- see BrokerSymbolSpec's own docstring for why. Every adapter converts its
    # own native units into canonical lots here; nothing above this layer ever sees a broker-
    # native volume representation (e.g. cTrader centilots).
    async def symbol_spec(self, instrument_id: str) -> BrokerSymbolSpec: ...
    async def quote(self, instrument_id: str) -> BrokerQuote: ...
    async def historical_bars(self, instrument_id: str, *, bar_size: str, duration: str) -> list[BrokerBar]: ...
    async def reconcile(self, account_id: str) -> BrokerReconciliationResult: ...


@runtime_checkable
class BrokerOrderAdapter(Protocol):
    async def submit_order(self, command: BrokerOrderCommand) -> BrokerOrderReceipt: ...
    async def cancel_order(self, command: BrokerCancelCommand) -> BrokerCancelReceipt: ...
    # Broker Independence Assessment Phase 1, item 2: previously absent -- every SL/TP change and
    # every position close was hand-built as a raw MT5 order_send() request dict inside
    # adaptive_management/service.py and portfolio_execution/service.py, bypassing this Protocol
    # entirely. Both new methods are additive to the Protocol only in this phase -- no call site
    # is rewired to use them yet (that's Phase 3/4, per the migration plan).
    async def modify_position(self, command: BrokerModifyPositionCommand) -> BrokerModifyReceipt: ...
    async def close_position(self, command: BrokerClosePositionCommand) -> BrokerCloseReceipt: ...
