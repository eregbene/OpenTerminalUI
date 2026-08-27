"""CTraderAdapter -- read-only account/market-data access (Phase 2) PLUS, as of 2026-08-27, real
order-execution mutation for the DEMO-only vertical slice (Phase 3): submit_order/cancel_order/
modify_position/close_position now perform real cTrader Open API calls, gated behind TWO
independent safety layers that must BOTH be satisfied before any order reaches the wire:
  1. CTraderConfig.order_submission_enabled (CTRADER_ORDER_SUBMISSION_ENABLED, default False) --
     an explicit opt-in flag, same pattern as MT5_ORDER_SUBMISSION_ENABLED.
  2. The OAuth token's own server-side scope -- a token issued with scope="accounts" (read-only)
     is rejected by cTrader's OWN servers regardless of what this code does; scope="trading" is
     a SEPARATE re-authorization the operator must perform (see ctrader_oauth_setup.py --scope
     trading). This code cannot bypass that; it is not a code-side gate.
Both gates check `_verified_demo` (set by _authorize_account_and_verify_demo, itself independent
of CTRADER_ENVIRONMENT) before allowing mutation -- a LIVE account is refused even if someone
sets order_submission_enabled=true, matching MT5's own layered demo-enforcement.

Price scale: ProtoOATrendbar/ProtoOASpotEvent prices are fixed-point integers, ALWAYS divided by
100000 regardless of the symbol's own `digits` (verified: help.ctrader.com/open-api/symbol-data --
"divide the low price of a trendbar by 100000"). This is a DIFFERENT convention from moneyDigits
(account monetary fields, exponent varies per trader) and from lotSize/volume-in-cents (see
volume.py) -- three separate scaling conventions in this protocol, never interchange them.

Price scale: ProtoOATrendbar/ProtoOASpotEvent prices are fixed-point integers, ALWAYS divided by
100000 regardless of the symbol's own `digits` (verified: help.ctrader.com/open-api/symbol-data --
"divide the low price of a trendbar by 100000"). This is a DIFFERENT convention from moneyDigits
(account monetary fields, exponent varies per trader) and from lotSize/volume-in-cents (see
volume.py) -- three separate scaling conventions in this protocol, never interchange them.

Every price/volume value that crosses out of this adapter goes through symbols.py/volume.py's
canonical conversions -- nothing above this file ever sees a cTrader-native raw integer.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from backend.brokers.base import BrokerReadAdapter
from backend.brokers.capabilities import BrokerCapabilities
from backend.brokers.ctrader.config import CTraderConfig, ctrader_config
from backend.brokers.ctrader.exceptions import (
    CTraderAuthError, CTraderCapabilityError, CTraderEnvironmentMismatchError, CTraderReadOnlyViolation, CTraderUnavailableError,
)
from backend.brokers.ctrader.oauth import refresh_access_token
from backend.brokers.ctrader.symbols import ctrader_symbol_to_spec
from backend.brokers.ctrader.transport import CTraderTransport
from backend.brokers.ctrader.volume import lots_to_raw_volume, money_from_raw, raw_volume_to_lots
from backend.brokers.health import BrokerHealth
from backend.brokers.models import (
    BrokerAccount, BrokerAccountSnapshot, BrokerBar, BrokerCancelCommand, BrokerCancelReceipt,
    BrokerClosePositionCommand, BrokerCloseReceipt, BrokerConnectionState, BrokerContract, BrokerEnvironment,
    BrokerExecution, BrokerHealthState, BrokerModifyPositionCommand, BrokerModifyReceipt, BrokerOrder,
    BrokerOrderCommand, BrokerOrderReceipt, BrokerOrderState, BrokerPosition, BrokerQuote, BrokerReconciliationResult,
    BrokerSymbolSpec, MarketDataMode, DataQuality,
)

logger = logging.getLogger(__name__)

_PRICE_SCALE = Decimal(100000)  # fixed protocol-wide constant -- see module docstring
_TRENDBAR_PERIOD = {"M1": "M1", "M5": "M5", "M15": "M15", "M30": "M30", "H1": "H1", "H4": "H4", "D1": "D1"}


def _price_from_raw(raw: int) -> Decimal:
    return Decimal(raw) / _PRICE_SCALE


def _price_to_raw(price: Decimal) -> int:
    return int((price * _PRICE_SCALE).to_integral_value())


class CTraderAdapter:
    name = "ctrader"

    def __init__(self, config: CTraderConfig | None = None) -> None:
        self._config = config or ctrader_config()
        self._transport: CTraderTransport | None = None
        self._app_authorized = False
        self._authorized_account_id: int | None = None
        self._verified_demo = False
        self._connect_lock = asyncio.Lock()
        self.capabilities = BrokerCapabilities(
            broker="ctrader", environment=self._config.environment.upper(),
            asset_types={"FX", "CFD"}, order_types={"MARKET"}, time_in_force={"IMMEDIATE_OR_CANCEL"},
            market_data={"REALTIME"}, historical_data={"TRENDBARS"},
            supports_cancel=True, supports_modify=True, supports_streaming=True,
            # live_trading_enabled means real-money LIVE trading -- always False here regardless
            # of order_submission_enabled/config.environment. DEMO order submission is gated
            # separately, per-call, by _require_order_submission_enabled() -- see module docstring.
            live_trading_enabled=False,
        )

    # ------------------------------------------------------------------ connection lifecycle ---
    async def connect(self) -> BrokerHealth:
        async with self._connect_lock:
            if not self._config.is_demo:
                raise CTraderEnvironmentMismatchError(
                    f"CTRADER_ENVIRONMENT={self._config.environment!r} -- this phase refuses anything but 'demo'"
                )
            if not self._config.has_app_credentials or not self._config.has_account_tokens or not self._config.account_id:
                raise CTraderUnavailableError(
                    "cTrader credentials not configured -- set CTRADER_CLIENT_ID/CLIENT_SECRET/ACCESS_TOKEN/"
                    "REFRESH_TOKEN/ACCOUNT_ID (see backend/scripts/ctrader_oauth_setup.py)"
                )

            from ctrader_open_api import EndPoints

            if self._transport is None:
                self._transport = CTraderTransport(host=EndPoints.PROTOBUF_DEMO_HOST, port=EndPoints.PROTOBUF_PORT)
                self._transport.start()

            await self._wait_connected()
            await self._authorize_app()
            await self._authorize_account_and_verify_demo()
            return await self.health()

    async def _wait_connected(self, timeout: float = 15.0) -> None:
        assert self._transport is not None
        deadline = asyncio.get_event_loop().time() + timeout
        while not self._transport.is_connected:
            if asyncio.get_event_loop().time() > deadline:
                raise CTraderUnavailableError(f"cTrader transport did not connect within {timeout}s")
            await asyncio.sleep(0.1)

    async def _authorize_app(self) -> None:
        if self._app_authorized:
            return
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAApplicationAuthReq

        req = ProtoOAApplicationAuthReq()
        req.clientId = self._config.client_id
        req.clientSecret = self._config.client_secret
        response = await self._send(req)
        _raise_if_error(response)
        self._app_authorized = True

    async def _authorize_account_and_verify_demo(self) -> None:
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAAccountAuthReq, ProtoOAGetAccountListByAccessTokenReq

        account_id = int(self._config.account_id)  # type: ignore[arg-type]

        # Independently verify demo/live identity from cTrader's own account list -- never trust
        # CTRADER_ENVIRONMENT=demo alone (explicit user safety requirement: "Refuse initialization
        # if environment/account identity is inconsistent").
        list_req = ProtoOAGetAccountListByAccessTokenReq()
        list_req.accessToken = self._config.access_token
        list_res = _extract(await self._send(list_req))
        _raise_if_error(list_res)
        matching = [acc for acc in list_res.ctidTraderAccount if int(acc.ctidTraderAccountId) == account_id]
        if not matching:
            raise CTraderEnvironmentMismatchError(f"account {account_id} was not found in this access token's account list -- refusing to proceed")
        if bool(matching[0].isLive):
            raise CTraderEnvironmentMismatchError(
                f"account {account_id} reports isLive=True (a LIVE account) but CTRADER_ENVIRONMENT=demo -- refusing to connect"
            )
        self._verified_demo = True

        auth_req = ProtoOAAccountAuthReq()
        auth_req.ctidTraderAccountId = account_id
        auth_req.accessToken = self._config.access_token
        response = await self._send(auth_req)
        _raise_if_error(response)
        self._authorized_account_id = account_id

    async def _send(self, message: Any) -> Any:
        if self._transport is None:
            raise CTraderUnavailableError("cTrader transport not started -- call connect() first")
        try:
            return await self._transport.send(message)
        except CTraderUnavailableError:
            raise
        except Exception as exc:
            raise CTraderUnavailableError(str(exc)) from exc

    async def _refresh_token_if_needed(self) -> None:
        """Access tokens expire; refresh tokens are single-use/rotating (cTrader's own documented
        behavior -- see oauth.py's module docstring). This phase refreshes reactively on an auth
        error rather than tracking expiry proactively (simplest correct behavior for a read-only,
        low-request-volume phase); the NEW refresh_token must be persisted by the caller/operator
        -- this method only updates the in-memory config, it does not write .env for you."""
        result = refresh_access_token(
            client_id=self._config.client_id, client_secret=self._config.client_secret,
            redirect_uri=self._config.redirect_uri, refresh_token=self._config.refresh_token,
        )
        object.__setattr__(self._config, "access_token", result.access_token)
        object.__setattr__(self._config, "refresh_token", result.refresh_token)
        logger.warning(
            "cTrader access token refreshed -- a NEW refresh_token was issued (single-use/rotating); "
            "persist CTRADER_REFRESH_TOKEN=%s to .env or the next refresh will fail", result.refresh_token,
        )

    async def disconnect(self) -> BrokerHealth:
        if self._transport is not None:
            self._transport.stop()
        self._app_authorized = False
        self._authorized_account_id = None
        return await self.health()

    async def reconnect(self) -> BrokerHealth:
        await self.disconnect()
        self._transport = None
        return await self.connect()

    async def health(self) -> BrokerHealth:
        connected = self._transport is not None and self._transport.is_connected
        return BrokerHealth(
            broker="ctrader",
            state=BrokerHealthState.HEALTHY if (connected and self._authorized_account_id) else (
                BrokerHealthState.DEGRADED if connected else BrokerHealthState.DISCONNECTED
            ),
            connection_state=BrokerConnectionState.CONNECTED if connected else BrokerConnectionState.DISCONNECTED,
            environment=BrokerEnvironment.PAPER if self._verified_demo else BrokerEnvironment.UNVERIFIED,
            account_verification_status="VERIFIED_DEMO" if self._verified_demo else "UNVERIFIED",
            order_submission_status="ENABLED" if (self._verified_demo and self._config.order_submission_enabled) else "DISABLED",
        )

    # ------------------------------------------------------------------------------ accounts ---
    async def accounts(self) -> list[BrokerAccount]:
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAGetAccountListByAccessTokenReq

        req = ProtoOAGetAccountListByAccessTokenReq()
        req.accessToken = self._config.access_token
        res = _extract(await self._send(req))
        _raise_if_error(res)
        return [
            BrokerAccount(
                account_id=str(acc.ctidTraderAccountId), alias=str(acc.traderLogin), broker="ctrader",
                environment=BrokerEnvironment.PAPER if not acc.isLive else BrokerEnvironment.LIVE,
                account_type="DEMO" if not acc.isLive else "LIVE", allowed=not acc.isLive,
            )
            for acc in res.ctidTraderAccount
        ]

    async def account_snapshot(self, account_id: str) -> BrokerAccountSnapshot:
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOATraderReq

        req = ProtoOATraderReq()
        req.ctidTraderAccountId = int(account_id)
        res = _extract(await self._send(req))
        _raise_if_error(res)
        trader = res.trader
        digits = int(trader.moneyDigits)
        balance = money_from_raw(int(trader.balance), digits)
        positions = await self.positions(account_id)
        unrealized = sum((p.unrealized_pnl or Decimal(0)) for p in positions) if positions else Decimal(0)

        return BrokerAccountSnapshot(
            account_id=account_id, broker="ctrader", environment=BrokerEnvironment.PAPER,
            net_liquidation=balance + unrealized, buying_power=balance, available_funds=balance,
            excess_liquidity=balance, unrealized_pnl=unrealized, positions=positions,
        )

    # ----------------------------------------------------------------------------- positions ---
    async def positions(self, account_id: str) -> list[BrokerPosition]:
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAReconcileReq

        req = ProtoOAReconcileReq()
        req.ctidTraderAccountId = int(account_id)
        res = _extract(await self._send(req))
        _raise_if_error(res)

        open_positions = [pos for pos in res.position if int(pos.positionStatus) == 1]  # POSITION_STATUS_OPEN == 1
        if not open_positions:
            return []

        # Real per-symbol lotSize is REQUIRED to convert raw volume into canonical lots -- never
        # approximate it (Part "critical symbol/volume validation": no cTrader raw/centilot
        # representation may leak above this adapter). ProtoOASymbolByIdReq accepts multiple
        # symbolId in one request, so this is a single extra round-trip regardless of how many
        # distinct symbols are open.
        symbol_ids = sorted({int(pos.tradeData.symbolId) for pos in open_positions})
        lot_size_by_symbol = await self._lot_sizes_for(symbol_ids)

        out: list[BrokerPosition] = []
        for pos in open_positions:
            symbol_id = int(pos.tradeData.symbolId)
            lot_size_raw = lot_size_by_symbol.get(symbol_id)
            if lot_size_raw is None:
                logger.warning("cTrader position %s references symbolId=%s with no resolvable lotSize -- skipping rather than guessing a volume", pos.positionId, symbol_id)
                continue
            side = int(pos.tradeData.tradeSide)  # BUY=1, SELL=2
            volume_lots = raw_volume_to_lots(int(pos.tradeData.volume), lot_size_raw)
            quantity = volume_lots if side == 1 else -volume_lots
            out.append(BrokerPosition(
                canonical_position_id=f"ctrader:{account_id}:{pos.positionId}",
                broker_position_id=str(pos.positionId), account_id=account_id, broker="ctrader",
                instrument_id=f"FX:CTRADER_SYMBOL_{symbol_id}",  # resolved to a real canonical symbol name by the caller via a separate symbol lookup -- see resolve_contract()
                quantity=quantity, average_cost=Decimal(str(pos.price)),
                unrealized_pnl=None,  # cTrader does not include unrealized P&L directly on ProtoOAPosition; computing it requires a live quote (deferred to a later phase's account_snapshot enrichment) -- None, never fabricated as 0
                stop_loss=Decimal(str(pos.stopLoss)) if pos.stopLoss else None,
                take_profit=Decimal(str(pos.takeProfit)) if pos.takeProfit else None,
                currency="USD",
            ))
        return out

    async def _lot_sizes_for(self, symbol_ids: list[int]) -> dict[int, int]:
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOASymbolByIdReq

        req = ProtoOASymbolByIdReq()
        req.ctidTraderAccountId = int(self._config.account_id)  # type: ignore[arg-type]
        for sid in symbol_ids:
            req.symbolId.append(sid)
        res = _extract(await self._send(req))
        _raise_if_error(res)
        return {int(sym.symbolId): int(sym.lotSize) for sym in res.symbol if sym.lotSize}

    async def open_orders(self, account_id: str) -> list[BrokerOrder]:
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAReconcileReq

        req = ProtoOAReconcileReq()
        req.ctidTraderAccountId = int(account_id)
        res = _extract(await self._send(req))
        _raise_if_error(res)
        return []  # Phase 2 scope: positions are the priority read; pending-order mapping deferred, not yet needed by any caller

    async def executions(self, account_id: str, since: datetime | None = None) -> list[BrokerExecution]:
        return []  # deferred -- not required for Phase 2's read-only milestone

    async def reconcile(self, account_id: str) -> BrokerReconciliationResult:
        # Matches MT5Adapter's own Phase-1 posture: real broker-side positions are fetchable
        # (positions() above), but comparing them against Bensim's own journal is out of scope
        # for this read-only phase (no journal integration for cTrader exists yet).
        positions = await self.positions(account_id)
        return BrokerReconciliationResult(account_id=account_id, status="MATCHED_EMPTY" if not positions else "NOT_COMPARED")

    # ------------------------------------------------------------------------------- symbols ---
    async def resolve_contract(self, instrument_id: str) -> BrokerContract:
        symbol_id, name = await self._resolve_symbol_id(instrument_id)
        return BrokerContract(
            instrument_id=instrument_id, symbol=name, asset_type="FX", exchange="CTRADER",
            currency="USD", security_type="CASH", con_id=symbol_id, source="ctrader",
        )

    async def symbol_spec(self, instrument_id: str) -> BrokerSymbolSpec:
        symbol_id, name = await self._resolve_symbol_id(instrument_id)
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOASymbolByIdReq

        req = ProtoOASymbolByIdReq()
        req.ctidTraderAccountId = int(self._config.account_id)  # type: ignore[arg-type]
        req.symbolId.append(symbol_id)
        res = _extract(await self._send(req))
        _raise_if_error(res)
        if not res.symbol:
            raise CTraderUnavailableError(f"symbol_spec({instrument_id}): cTrader returned no ProtoOASymbol for symbolId={symbol_id}")
        return ctrader_symbol_to_spec(res.symbol[0], instrument_id=instrument_id)

    async def _resolve_symbol_id(self, instrument_id: str) -> tuple[int, str]:
        name = instrument_id.split(":")[-1].upper()
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOASymbolsListReq

        req = ProtoOASymbolsListReq()
        req.ctidTraderAccountId = int(self._config.account_id)  # type: ignore[arg-type]
        res = _extract(await self._send(req))
        _raise_if_error(res)
        for sym in res.symbol:
            if str(sym.symbolName).upper() == name:
                return int(sym.symbolId), name
        raise CTraderUnavailableError(f"symbol {name!r} not found in this account's cTrader symbol list")

    # --------------------------------------------------------------------------- market data ---
    async def quote(self, instrument_id: str) -> BrokerQuote:
        symbol_id, _name = await self._resolve_symbol_id(instrument_id)
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOASpotEvent, ProtoOASubscribeSpotsReq

        req = ProtoOASubscribeSpotsReq()
        req.ctidTraderAccountId = int(self._config.account_id)  # type: ignore[arg-type]
        req.symbolId.append(symbol_id)
        await self._send(req)

        event = await self._wait_for_message(
            lambda msg: getattr(msg, "payloadType", None) == ProtoOASpotEvent().payloadType,
            timeout=15.0,
        )
        spot = _extract(event)
        return BrokerQuote(
            instrument_id=instrument_id,
            bid=_price_from_raw(spot.bid) if spot.bid else None,
            ask=_price_from_raw(spot.ask) if spot.ask else None,
            mode=MarketDataMode.REALTIME, quality=DataQuality.VALID,
        )

    async def historical_bars(self, instrument_id: str, *, bar_size: str, duration: str) -> list[BrokerBar]:
        symbol_id, _name = await self._resolve_symbol_id(instrument_id)
        period_name = _TRENDBAR_PERIOD.get(bar_size.upper())
        if period_name is None:
            raise CTraderUnavailableError(f"unsupported bar_size for cTrader: {bar_size!r}")

        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAGetTrendbarsReq
        from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATrendbarPeriod

        req = ProtoOAGetTrendbarsReq()
        req.ctidTraderAccountId = int(self._config.account_id)  # type: ignore[arg-type]
        req.symbolId = symbol_id
        req.period = getattr(ProtoOATrendbarPeriod, period_name)
        req.count = _count_from_duration(duration)
        res = _extract(await self._send(req))
        _raise_if_error(res)

        bars: list[BrokerBar] = []
        for bar in res.trendbar:
            low = _price_from_raw(bar.low)
            bars.append(BrokerBar(
                instrument_id=instrument_id,
                timestamp=datetime.fromtimestamp(int(bar.utcTimestampInMinutes) * 60, tz=timezone.utc),
                open=low + _price_from_raw(bar.deltaOpen), high=low + _price_from_raw(bar.deltaHigh),
                low=low, close=low + _price_from_raw(bar.deltaClose),
                volume=Decimal(bar.volume), mode=MarketDataMode.REALTIME, quality=DataQuality.VALID,
            ))
        return bars

    async def _wait_for_message(self, predicate: Any, *, timeout: float) -> Any:
        """One-shot wait for the next transport-pushed message matching `predicate` (used for
        ProtoOASpotEvent, which arrives asynchronously, not as a send() response)."""
        assert self._transport is not None
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=32)
        original_callback = self._transport._external_message_callback

        def _on_message(message: Any) -> None:
            if original_callback is not None:
                original_callback(message)
            loop.call_soon_threadsafe(queue.put_nowait, message)

        self._transport._external_message_callback = _on_message
        try:
            deadline = loop.time() + timeout
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise CTraderUnavailableError(f"timed out after {timeout}s waiting for a matching cTrader message")
                message = await asyncio.wait_for(queue.get(), timeout=remaining)
                if predicate(message):
                    return message
        finally:
            self._transport._external_message_callback = original_callback

    # ------------------------------------------------------- order mutation (DEMO vertical slice) ---
    def _require_order_submission_enabled(self) -> None:
        """Both independent gates must pass -- see module docstring. Checked fresh on every call
        (never cached) so flipping the env var takes effect on the next order without a
        reconnect, same convention as every other reversible flag in this codebase."""
        if not self._verified_demo:
            raise CTraderReadOnlyViolation("cTrader account identity not verified as DEMO -- call connect() first")
        if not self._config.order_submission_enabled:
            raise CTraderReadOnlyViolation("CTRADER_ORDER_SUBMISSION_ENABLED is not set -- order mutation is off by default")

    def _normalize_volume(self, quantity: Decimal, spec: BrokerSymbolSpec) -> Decimal:
        """Floors to volume_step, NEVER rounds up to volume_min -- a request that floors below
        the broker's minimum tradeable size is REJECTED, not inflated. This is the same
        invariant MT5's calculate_risk_size enforces (the XAUUSD-incident rule) and the one the
        strategy-tier hard-cap fix (2026-08-25/26) depends on: a Tier B/C position's risk must
        never be silently increased past its cap just to satisfy a broker's volume floor."""
        if quantity <= 0:
            raise CTraderCapabilityError("INVALID_QUANTITY", f"requested quantity must be positive, got {quantity}", status_code=422)
        step = spec.volume_step
        floored = (quantity / step).to_integral_value(rounding=ROUND_FLOOR) * step
        if floored < spec.volume_min:
            raise CTraderCapabilityError(
                "VOLUME_BELOW_MINIMUM",
                f"requested quantity {quantity} floors to {floored} lots, below this symbol's volume_min={spec.volume_min} -- "
                f"rejecting rather than inflating (would exceed the caller's approved risk)",
                status_code=422,
            )
        return min(floored, spec.volume_max)

    async def _raw_lot_size(self, symbol_id: int) -> int:
        sizes = await self._lot_sizes_for([symbol_id])
        lot_size_raw = sizes.get(symbol_id)
        if lot_size_raw is None:
            raise CTraderUnavailableError(f"cTrader did not report a lotSize for symbolId={symbol_id}")
        return lot_size_raw

    async def _wait_for_execution(self, *, account_id: int, timeout: float = 20.0) -> Any:
        """Order confirmation arrives asynchronously as a ProtoOAExecutionEvent (ORDER_FILLED/
        ORDER_REJECTED/...) or a ProtoOAOrderErrorEvent, not as send()'s direct response --
        same one-shot-queue pattern quote()'s ProtoOASpotEvent wait already uses. Filters to this
        account only, since a single transport can authenticate multiple cTrader accounts."""
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAExecutionEvent, ProtoOAOrderErrorEvent

        exec_type = ProtoOAExecutionEvent().payloadType
        error_type = ProtoOAOrderErrorEvent().payloadType

        def _matches(msg: Any) -> bool:
            payload_type = getattr(msg, "payloadType", None)
            if payload_type not in (exec_type, error_type):
                return False
            extracted = _extract(msg)
            return int(getattr(extracted, "ctidTraderAccountId", -1)) == account_id

        raw = await self._wait_for_message(_matches, timeout=timeout)
        return _extract(raw)

    async def submit_order(self, command: BrokerOrderCommand) -> BrokerOrderReceipt:
        self._require_order_submission_enabled()
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOANewOrderReq, ProtoOAOrderErrorEvent
        from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOAOrderType, ProtoOATimeInForce, ProtoOATradeSide

        account_id = int(command.account_id)
        symbol_id, _name = await self._resolve_symbol_id(command.instrument_id)
        spec = await self.symbol_spec(command.instrument_id)
        lot_size_raw = await self._raw_lot_size(symbol_id)

        # ALWAYS the caller's risk-vetted approved_quantity, never the raw requested quantity --
        # mirrors calculate_risk_size's own "risk is decided upstream, this layer only executes
        # it faithfully" boundary.
        normalized_lots = self._normalize_volume(command.approved_quantity, spec)
        raw_volume = lots_to_raw_volume(normalized_lots, lot_size_raw)

        req = ProtoOANewOrderReq()
        req.ctidTraderAccountId = account_id
        req.symbolId = symbol_id
        req.orderType = ProtoOAOrderType.MARKET
        req.tradeSide = ProtoOATradeSide.BUY if command.side.upper() in ("BUY", "LONG") else ProtoOATradeSide.SELL
        req.volume = raw_volume
        req.timeInForce = ProtoOATimeInForce.IMMEDIATE_OR_CANCEL
        if command.stop_loss is not None:
            req.stopLoss = float(command.stop_loss)
        if command.take_profit is not None:
            req.takeProfit = float(command.take_profit)
        req.label = command.canonical_order_id[:50]
        req.comment = command.idempotency_key[:100]

        await self._send(req)
        event = await self._wait_for_execution(account_id=account_id)
        if type(event).__name__ == "ProtoOAOrderErrorEvent" or getattr(event, "errorCode", ""):
            raise CTraderAuthError(f"cTrader order rejected: {getattr(event, 'errorCode', 'UNKNOWN')} -- {getattr(event, 'description', '')}")

        order_pb = event.order
        deal_pb = event.deal if event.HasField("deal") else None
        position_id = int(order_pb.positionId) if order_pb.positionId else (int(deal_pb.positionId) if deal_pb else None)
        filled_lots = raw_volume_to_lots(int(deal_pb.filledVolume if deal_pb else order_pb.executedVolume or raw_volume), lot_size_raw)
        # 2026-08-27 real bug found via live order test: ProtoOADeal.executionPrice is a plain
        # double, NOT the fixed-point-integer/1e5 convention _price_from_raw exists for (that
        # convention is confirmed to apply ONLY to ProtoOATrendbar/ProtoOASpotEvent per this
        # module's own docstring) -- same plain-double convention positions() already correctly
        # uses for pos.stopLoss/takeProfit via Decimal(str(...)), no scaling. Confirmed live:
        # a real $1.16438 fill was reported as $0.00001 before this fix (int(1.16438)/100000).
        fill_price = Decimal(str(deal_pb.executionPrice)) if deal_pb and deal_pb.executionPrice else None

        order = BrokerOrder(
            canonical_order_id=command.canonical_order_id, broker_order_id=str(order_pb.orderId), account_id=command.account_id,
            broker="ctrader", instrument_id=command.instrument_id, state=BrokerOrderState.FILLED,
            raw_status=str(getattr(event, "executionType", "")), average_price=fill_price,
            filled_quantity=filled_lots, remaining_quantity=Decimal(0), correlation_id=command.correlation_id,
        )
        execution = BrokerExecution(
            canonical_order_id=command.canonical_order_id, broker_order_id=str(order_pb.orderId), account_id=command.account_id,
            broker="ctrader", instrument_id=command.instrument_id, side=command.side, quantity=filled_lots,
            price=fill_price or Decimal(0), commission=money_from_raw(int(deal_pb.commission), 2) if deal_pb and deal_pb.commission else Decimal(0),
        )
        logger.warning(
            "cTrader DEMO order submitted account=%s symbol=%s side=%s volume=%s positionId=%s fill=%s",
            command.account_id, command.instrument_id, command.side, normalized_lots, position_id, fill_price,
        )
        return BrokerOrderReceipt(order=order, execution=execution, submission_state="FILLED")

    async def cancel_order(self, command: BrokerCancelCommand) -> BrokerCancelReceipt:
        self._require_order_submission_enabled()
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOACancelOrderReq

        req = ProtoOACancelOrderReq()
        req.ctidTraderAccountId = int(command.account_id)
        req.orderId = int(command.broker_order_id)
        await self._send(req)
        event = await self._wait_for_execution(account_id=int(command.account_id))
        if getattr(event, "errorCode", ""):
            raise CTraderAuthError(f"cTrader cancel rejected: {event.errorCode} -- {getattr(event, 'description', '')}")
        return BrokerCancelReceipt(canonical_order_id=command.canonical_order_id, broker_order_id=command.broker_order_id, state=BrokerOrderState.CANCELLED)

    async def modify_position(self, command: BrokerModifyPositionCommand) -> BrokerModifyReceipt:
        self._require_order_submission_enabled()
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAAmendPositionSLTPReq

        account_id = int(command.account_id)
        # None means "leave unchanged", never "clear" (see BrokerModifyPositionCommand's own
        # docstring) -- cTrader's amend request has no such distinction, so a None field here
        # must be filled in with the position's OWN CURRENT value before sending, or it would be
        # silently zeroed (cleared) instead of preserved.
        if command.stop_loss is None or command.take_profit is None:
            current = next((p for p in await self.positions(command.account_id) if p.broker_position_id == command.broker_position_id), None)
            if current is None:
                raise CTraderUnavailableError(f"cannot amend SL/TP: position {command.broker_position_id} not found in current positions")
            effective_sl = command.stop_loss if command.stop_loss is not None else current.stop_loss
            effective_tp = command.take_profit if command.take_profit is not None else current.take_profit
        else:
            effective_sl, effective_tp = command.stop_loss, command.take_profit

        req = ProtoOAAmendPositionSLTPReq()
        req.ctidTraderAccountId = account_id
        req.positionId = int(command.broker_position_id)
        if effective_sl is not None:
            req.stopLoss = float(effective_sl)
        if effective_tp is not None:
            req.takeProfit = float(effective_tp)
        await self._send(req)
        event = await self._wait_for_execution(account_id=account_id)
        if getattr(event, "errorCode", ""):
            raise CTraderAuthError(f"cTrader SL/TP amend rejected: {event.errorCode} -- {getattr(event, 'description', '')}")

        refreshed = next((p for p in await self.positions(command.account_id) if p.broker_position_id == command.broker_position_id), None)
        if refreshed is None:
            raise CTraderUnavailableError(f"position {command.broker_position_id} not found after amend -- cannot confirm new SL/TP")
        return BrokerModifyReceipt(position=refreshed, submission_state="FILLED")

    async def close_position(self, command: BrokerClosePositionCommand) -> BrokerCloseReceipt:
        self._require_order_submission_enabled()
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAClosePositionReq

        account_id = int(command.account_id)
        current = next((p for p in await self.positions(command.account_id) if p.broker_position_id == command.broker_position_id), None)
        if current is None:
            raise CTraderUnavailableError(f"cannot close: position {command.broker_position_id} not found in current positions")
        current_lots = abs(current.quantity)

        symbol_id, _name = await self._resolve_symbol_id(command.instrument_id)
        lot_size_raw = await self._raw_lot_size(symbol_id)
        spec = await self.symbol_spec(command.instrument_id)

        # quantity=None means full close (see BrokerClosePositionCommand's own docstring) --
        # close EXACTLY the position's current volume, never a re-derived/guessed figure.
        close_lots = current_lots if command.quantity is None else self._normalize_volume(command.quantity, spec)
        if close_lots > current_lots:
            close_lots = current_lots  # never request more than the position actually holds
        raw_volume = lots_to_raw_volume(close_lots, lot_size_raw)

        req = ProtoOAClosePositionReq()
        req.ctidTraderAccountId = account_id
        req.positionId = int(command.broker_position_id)
        req.volume = raw_volume
        await self._send(req)
        event = await self._wait_for_execution(account_id=account_id)
        if getattr(event, "errorCode", ""):
            raise CTraderAuthError(f"cTrader close rejected: {event.errorCode} -- {getattr(event, 'description', '')}")

        deal_pb = event.deal if event.HasField("deal") else None
        # 2026-08-27 real bug found via live order test: ProtoOADeal.executionPrice is a plain
        # double, NOT the fixed-point-integer/1e5 convention _price_from_raw exists for (that
        # convention is confirmed to apply ONLY to ProtoOATrendbar/ProtoOASpotEvent per this
        # module's own docstring) -- same plain-double convention positions() already correctly
        # uses for pos.stopLoss/takeProfit via Decimal(str(...)), no scaling. Confirmed live:
        # a real $1.16438 fill was reported as $0.00001 before this fix (int(1.16438)/100000).
        fill_price = Decimal(str(deal_pb.executionPrice)) if deal_pb and deal_pb.executionPrice else None
        execution = BrokerExecution(
            canonical_order_id=command.canonical_position_id, broker_order_id=str(event.order.orderId) if event.HasField("order") else None,
            account_id=command.account_id, broker="ctrader", instrument_id=command.instrument_id,
            side="SELL" if current.quantity > 0 else "BUY", quantity=close_lots, price=fill_price or Decimal(0),
        )
        remaining_lots = current_lots - close_lots
        remaining_position = None
        if remaining_lots > 0:
            remaining_position = next((p for p in await self.positions(command.account_id) if p.broker_position_id == command.broker_position_id), None)
        logger.warning(
            "cTrader DEMO position closed account=%s positionId=%s closed_lots=%s remaining_lots=%s fill=%s",
            command.account_id, command.broker_position_id, close_lots, remaining_lots, fill_price,
        )
        return BrokerCloseReceipt(
            canonical_position_id=command.canonical_position_id, broker_position_id=command.broker_position_id,
            closed_quantity=close_lots, remaining_quantity=remaining_lots, execution=execution,
            remaining_position=remaining_position, submission_state="FILLED",
        )


def _extract(message: Any) -> Any:
    """send()'s exact return shape (raw ProtoMessage envelope vs. already-decoded typed message)
    isn't fully confirmed from documentation alone -- defensively try Protobuf.extract() first
    (the documented pattern for messages arriving via the message-received callback) and fall
    back to the message as-is if extraction isn't applicable. To be validated against the real
    live smoke test."""
    try:
        from ctrader_open_api import Protobuf

        return Protobuf.extract(message)
    except Exception:
        return message


def _raise_if_error(message: Any) -> None:
    payload_type_name = type(message).__name__
    if payload_type_name in ("ProtoOAErrorRes", "ProtoErrorRes"):
        code = getattr(message, "errorCode", "UNKNOWN")
        description = getattr(message, "description", "")
        raise CTraderAuthError(f"cTrader error response {code}: {description}")
