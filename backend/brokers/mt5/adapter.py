from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

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
    BrokerConnectionState,
    BrokerContract,
    BrokerEnvironment,
    BrokerExecution,
    BrokerHealthState,
    BrokerModifyPositionCommand,
    BrokerModifyReceipt,
    BrokerOrder,
    BrokerOrderCommand,
    BrokerOrderReceipt,
    BrokerPosition,
    BrokerQuote,
    BrokerReconciliationResult,
    BrokerSymbolSpec,
    DataQuality,
    MarketDataMode,
    now_utc,
)
from backend.brokers.mt5.account import account_from_raw
from backend.brokers.mt5.bridge import MT5Bridge
from backend.brokers.mt5.candles import candle_from_raw, mt5_timeframe
from backend.brokers.mt5.client import MT5Client
from backend.brokers.mt5.config import MT5Config, mt5_config
from backend.brokers.mt5.diagnostics import terminal_status_from_raw
from backend.brokers.mt5.exceptions import MT5ReadOnlyViolation, MT5UnavailableError
from backend.brokers.mt5.history import history_item_from_raw
from backend.brokers.mt5.market_data import candle_quality, quote_quality
from backend.brokers.mt5.models import MT5Account, MT5Candle, MT5ForexInstrument, MT5ForexUniverse, MT5HistoryItem, MT5Order, MT5Position, MT5Quote, MT5SchedulerStatus, MT5Symbol, MT5TerminalStatus
from backend.brokers.mt5.normalization import classify_symbol
from backend.brokers.mt5.orders import order_from_raw
from backend.brokers.mt5.persistence import persist_candles
from backend.brokers.mt5.positions import position_from_raw
from backend.brokers.mt5.quotes import quote_from_tick
from backend.brokers.mt5.symbols import DEFAULT_SYMBOLS, symbol_from_raw


class MT5Adapter:
    name = "mt5"

    def __init__(self, config: MT5Config | None = None, client: MT5Client | None = None) -> None:
        self.config = config or mt5_config()
        self.client = client or MT5Client(self.config)
        self.bridge = MT5Bridge(self.config)
        self.capabilities = BrokerCapabilities(
            broker="mt5",
            environment="DEMO",
            account_types={"DEMO"},
            asset_types={"FOREX", "METALS"},
            order_types=set(),
            time_in_force=set(),
            market_data={"TICK", "BID", "ASK", "SPREAD"},
            historical_data={"M1", "M5", "M15", "H1", "H4", "D1"},
            supports_cancel=False,
            supports_modify=False,
            supports_streaming=False,
            supports_currency_conversion=False,
            live_trading_enabled=False,
        )

    def reload_config(self) -> None:
        self.config = mt5_config()
        self.client.config = self.config
        self.bridge = MT5Bridge(self.config)

    async def connect(self) -> BrokerHealth:
        try:
            await asyncio.to_thread(self.client.connect)
            return await self.health()
        except MT5UnavailableError as exc:
            return BrokerHealth(
                broker="mt5",
                state=BrokerHealthState.DISABLED if not self.config.enabled else BrokerHealthState.DISCONNECTED,
                connection_state=BrokerConnectionState.DISABLED if not self.config.enabled else BrokerConnectionState.FAILED,
                environment=BrokerEnvironment.PAPER,
                last_error=exc.message,
                order_submission_status="READ_ONLY",
            )

    async def disconnect(self) -> BrokerHealth:
        await asyncio.to_thread(self.client.shutdown)
        return BrokerHealth(broker="mt5", state=BrokerHealthState.DISCONNECTED, connection_state=BrokerConnectionState.DISCONNECTED, environment=BrokerEnvironment.PAPER, order_submission_status="READ_ONLY")

    async def reconnect(self) -> BrokerHealth:
        await asyncio.to_thread(self.client.shutdown)
        return await self.connect()

    async def shutdown(self) -> None:
        await asyncio.to_thread(self.client.shutdown)

    async def health(self) -> BrokerHealth:
        try:
            terminal = await asyncio.to_thread(self.client.terminal_info)
            account = await asyncio.to_thread(self.client.account_info)
            version = await asyncio.to_thread(self.client.version)
            connected = bool(account)
            server_name = str((account or {}).get("server") or "").upper()
            demo_verified = self.config.account_mode == "DEMO" and ("DEMO" in server_name or (account or {}).get("trade_mode") == 0)
            return BrokerHealth(
                broker="mt5",
                state=BrokerHealthState.HEALTHY if connected and demo_verified else BrokerHealthState.DEGRADED,
                connection_state=BrokerConnectionState.CONNECTED if connected else BrokerConnectionState.DEGRADED,
                environment=BrokerEnvironment.PAPER,
                connected_host=str((terminal or {}).get("community_account", "") or (account or {}).get("server", "")) or None,
                server_version=str(version) if version else None,
                session_start=now_utc(),
                last_heartbeat=now_utc(),
                last_successful_request=now_utc(),
                account_verification_status="DEMO_VERIFIED" if demo_verified else "UNVERIFIED",
                order_submission_status="READ_ONLY",
            )
        except MT5UnavailableError as exc:
            return BrokerHealth(
                broker="mt5",
                state=BrokerHealthState.DISABLED if not self.config.enabled else BrokerHealthState.DISCONNECTED,
                connection_state=BrokerConnectionState.DISABLED if not self.config.enabled else BrokerConnectionState.FAILED,
                environment=BrokerEnvironment.PAPER,
                last_error=exc.message,
                order_submission_status="READ_ONLY",
            )

    async def terminal_status(self) -> MT5TerminalStatus:
        package_available = True
        try:
            terminal = await asyncio.to_thread(self.client.terminal_info)
            version = await asyncio.to_thread(self.client.version)
            account = await asyncio.to_thread(self.client.account_info)
            return terminal_status_from_raw(config=self.config, initialized=True, terminal=terminal, account=account, version=version, last_error=self.client.last_error())
        except MT5UnavailableError:
            package_available = False
            return MT5TerminalStatus(connected=False, initialized=False, package_available=package_available, terminal_info={}, version=None, last_error=self.client.last_error())

    async def mt5_account(self) -> MT5Account:
        raw = await asyncio.to_thread(self.client.account_info)
        if not raw:
            raise MT5UnavailableError("MT5 account_info unavailable")
        return account_from_raw(raw)

    async def accounts(self) -> list[BrokerAccount]:
        account = await self.mt5_account()
        account_id = str(account.login)
        return [BrokerAccount(account_id=account_id, alias=f"MT5***{account_id[-3:]}", environment=BrokerEnvironment.PAPER, base_currency=account.currency or "USD", paper_verified=True, allowed=True, account_type="DEMO")]

    async def account_snapshot(self, account_id: str) -> BrokerAccountSnapshot:
        account = await self.mt5_account()
        return BrokerAccountSnapshot(
            account_id=str(account.login),
            environment=BrokerEnvironment.PAPER,
            net_liquidation=account.equity,
            buying_power=account.free_margin,
            available_funds=account.free_margin,
            excess_liquidity=account.free_margin,
            initial_margin=account.margin,
            maintenance_margin=account.margin,
            cash=[],
        )

    async def symbols(self, group: str | None = None) -> list[MT5Symbol]:
        mt5 = self.client.ensure_ready()
        rows = await asyncio.to_thread(mt5.symbols_get, group=group) if group else await asyncio.to_thread(mt5.symbols_get)
        return [symbol_from_raw(row) for row in _rows(rows)]

    async def symbol_info(self, symbol: str) -> MT5Symbol:
        mt5 = self.client.ensure_ready()
        info = await asyncio.to_thread(mt5.symbol_info, symbol)
        if info is None:
            raise MT5UnavailableError(f"MT5 symbol_info unavailable for {symbol}: {self.client.last_error()}")
        return symbol_from_raw(info)

    async def symbol_select(self, symbol: str, enable: bool = True) -> bool:
        mt5 = self.client.ensure_ready()
        return bool(await asyncio.to_thread(mt5.symbol_select, symbol, enable))

    async def verify_symbols(self, symbols: tuple[str, ...] = DEFAULT_SYMBOLS) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for symbol in symbols:
            selected = await self.symbol_select(symbol, True)
            try:
                info = await self.symbol_info(symbol)
                tick = await self.latest_tick(symbol)
                out[symbol] = {"selected": selected, "available": True, "symbol": info.model_dump(mode="json"), "quote": tick.model_dump(mode="json")}
            except Exception as exc:
                out[symbol] = {"selected": selected, "available": False, "error": str(exc)}
        return out

    async def forex_universe(self) -> MT5ForexUniverse:
        symbols = await self.symbols()
        account = await self.mt5_account()
        symbols = _live_universe_symbols(symbols, self.config.trading_symbols)
        items: list[MT5ForexInstrument] = []
        naming_patterns: set[str] = set()
        mappings: dict[str, str] = {}
        now = now_utc()
        for symbol in symbols:
            classification = classify_symbol(symbol)
            if not classification.is_forex or not classification.canonical_pair:
                continue
            reasons: list[str] = []
            if classification.canonical_pair in self.config.excluded_symbols or classification.base_currency in self.config.excluded_currencies or classification.quote_currency in self.config.excluded_currencies:
                reasons.append("SYMBOL_DISABLED")
            if not symbol.visible:
                reasons.append("SYMBOL_NOT_VISIBLE")
            if symbol.trade_mode not in {None, 0, 1, 2, 3, 4}:
                reasons.append("SYMBOL_DISABLED")
            selected = await self.symbol_select(symbol.symbol, True)
            if not selected:
                reasons.append("SYMBOL_SELECT_FAILED")
            quote: MT5Quote | None = None
            try:
                quote = await self.latest_tick(symbol.symbol)
            except Exception:
                reasons.append("NO_QUOTE")
            reasons.extend(reason for reason in quote_quality(symbol, quote) if reason not in reasons)
            enabled_by_class = (
                classification.asset_class == "MAJOR" and self.config.include_majors
                or classification.asset_class == "MINOR" and self.config.include_minors
                or classification.asset_class == "EXOTIC" and self.config.include_exotics
                or classification.asset_class == "METAL" and self.config.include_metals
            )
            if not enabled_by_class:
                reasons.append("SYMBOL_DISABLED")
            naming_patterns.add(classification.naming_pattern)
            mappings[classification.broker_symbol] = classification.canonical_pair
            items.append(
                MT5ForexInstrument(
                    canonical_pair=classification.canonical_pair,
                    broker_symbol=symbol.symbol,
                    asset_class=classification.asset_class,
                    base_currency=classification.base_currency or classification.canonical_pair[:3],
                    quote_currency=classification.quote_currency or classification.canonical_pair[3:],
                    enabled="SYMBOL_DISABLED" not in reasons,
                    visible=symbol.visible,
                    tradable="SYMBOL_DISABLED" not in reasons and "SYMBOL_SELECT_FAILED" not in reasons,
                    market_open="MARKET_CLOSED" not in reasons,
                    selected=selected,
                    eligible=not reasons,
                    ineligibility_reasons=reasons,
                    specification_timestamp=now,
                    broker_server=account.server,
                    symbol=symbol,
                )
            )
        return MT5ForexUniverse(
            total_symbols=len(symbols),
            total_forex_pairs=len(items),
            majors=sum(1 for row in items if row.asset_class == "MAJOR"),
            minors=sum(1 for row in items if row.asset_class == "MINOR"),
            exotics=sum(1 for row in items if row.asset_class == "EXOTIC"),
            disabled_or_unavailable=sum(1 for row in items if not row.eligible),
            naming_patterns=sorted(naming_patterns),
            canonical_mappings=mappings,
            items=items,
        )

    async def latest_tick(self, symbol: str) -> MT5Quote:
        await self.symbol_select(symbol, True)
        mt5 = self.client.ensure_ready()
        tick = await asyncio.to_thread(mt5.symbol_info_tick, symbol)
        if tick is None:
            raise MT5UnavailableError(f"MT5 tick unavailable for {symbol}: {self.client.last_error()}")
        offset = await asyncio.to_thread(self.client.broker_utc_offset)
        return quote_from_tick(symbol, tick, broker_utc_offset=offset)

    async def quotes(self, symbols: list[str] | tuple[str, ...] | None = None) -> list[MT5Quote]:
        selected = list(symbols or [row.broker_symbol for row in (await self.forex_universe()).items if row.eligible])
        return [await self.latest_tick(symbol) for symbol in selected]

    async def quote(self, instrument_id: str) -> BrokerQuote:
        symbol = _symbol(instrument_id)
        quote = await self.latest_tick(symbol)
        return BrokerQuote(
            instrument_id=f"FX:{symbol}",
            bid=quote.bid,
            ask=quote.ask,
            last=quote.last,
            midpoint=(quote.bid + quote.ask) / Decimal("2") if quote.bid is not None and quote.ask is not None else None,
            timestamp=quote.time or now_utc(),
            mode=MarketDataMode.REALTIME,
            quality=DataQuality.VALID,
        )

    async def candles(self, symbol: str, timeframe: str, count: int = 100, *, completed_only: bool = True) -> list[MT5Candle]:
        await self.symbol_select(symbol, True)
        mt5 = self.client.ensure_ready()
        tf = mt5_timeframe(mt5, timeframe)
        start = 1 if completed_only else 0
        account = await self.mt5_account()
        rows = await asyncio.to_thread(mt5.copy_rates_from_pos, symbol, tf, start, count)
        offset = await asyncio.to_thread(self.client.broker_utc_offset)
        candles = [candle_from_raw(symbol, timeframe, row, server=account.server, complete_override=True if completed_only else None, broker_utc_offset=offset) for row in _rows(rows)]
        _persist_mt5_candles(candles, symbol=symbol)
        return candles

    async def calendar_status(self) -> dict[str, Any]:
        mt5 = self.client.ensure_ready()
        if hasattr(mt5, "calendar_status"):
            return await asyncio.to_thread(mt5.calendar_status)
        names = [name for name in dir(mt5) if name.lower().startswith("calendar")]
        return {"available": bool(names), "methods": names, "mode": "READ_ONLY"}

    async def calendar_values(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        mt5 = self.client.ensure_ready()
        if hasattr(mt5, "calendar_values"):
            rows = await asyncio.to_thread(mt5.calendar_values, start, end)
        elif hasattr(mt5, "calendar_value_history"):
            rows = await asyncio.to_thread(mt5.calendar_value_history, start, end)
        else:
            raise MT5UnavailableError("MT5 calendar functions are unavailable through the current Python connector")
        return [_row_dict(row) for row in _rows(rows)]

    async def copy_rates(self, symbol: str, timeframe: str, start: int = 1, count: int = 100) -> list[MT5Candle]:
        await self.symbol_select(symbol, True)
        mt5 = self.client.ensure_ready()
        tf = mt5_timeframe(mt5, timeframe)
        account = await self.mt5_account()
        rows = await asyncio.to_thread(mt5.copy_rates_from_pos, symbol, tf, max(0, start), count)
        offset = await asyncio.to_thread(self.client.broker_utc_offset)
        candles = [candle_from_raw(symbol, timeframe, row, server=account.server, complete_override=start > 0, broker_utc_offset=offset) for row in _rows(rows)]
        _persist_mt5_candles(candles, symbol=symbol)
        return candles

    async def copy_ticks(self, symbol: str, count: int = 100) -> list[dict[str, Any]]:
        mt5 = self.client.ensure_ready()
        if not hasattr(mt5, "copy_ticks_from"):
            return []
        now = datetime.now(timezone.utc)
        rows = await asyncio.to_thread(mt5.copy_ticks_from, symbol, now, count, getattr(mt5, "COPY_TICKS_ALL", 0))
        return [_row_dict(row) for row in _rows(rows)]

    async def eligible_candles(self, symbol: str, timeframe: str = "M15", count: int = 100) -> dict[str, Any]:
        rows = await self.candles(symbol, timeframe, count=count, completed_only=True)
        return {"items": rows, "quality_flags": candle_quality(rows, required=count)}

    async def historical_bars(self, instrument_id: str, *, bar_size: str, duration: str) -> list[BrokerBar]:
        symbol = _symbol(instrument_id)
        timeframe = _bar_size_to_timeframe(bar_size)
        count = _duration_to_count(duration, self.config.default_history_count)
        rows = await self.candles(symbol, timeframe, count=count)
        return [
            BrokerBar(
                instrument_id=f"FX:{symbol}",
                timestamp=row.time,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=Decimal(str(row.tick_volume)),
                completed=True,
                mode=MarketDataMode.REALTIME,
                quality=DataQuality.VALID,
            )
            for row in rows
        ]

    async def positions(self, account_id: str) -> list[BrokerPosition]:
        rows = await self.mt5_positions()
        return [
            BrokerPosition(
                # Broker Independence Assessment Phase 1: deterministic canonical id (same ticket
                # + account always maps to the same canonical_position_id across repeated calls,
                # unlike e.g. BrokerAccountSnapshot.snapshot_id, which is legitimately random-per-
                # snapshot) -- see BrokerPosition's own field docstring.
                canonical_position_id=f"mt5:{account_id}:{row.ticket}",
                broker_position_id=str(row.ticket),
                account_id=account_id,
                broker="mt5",
                instrument_id=f"FX:{row.symbol}",
                quantity=row.volume if row.type == 0 else -row.volume,
                average_cost=row.price_open,
                market_price=row.price_current,
                unrealized_pnl=row.profit,
                stop_loss=row.sl,
                take_profit=row.tp,
                currency="USD",
            )
            for row in rows
        ]

    async def mt5_positions(self) -> list[MT5Position]:
        mt5 = self.client.ensure_ready()
        rows = await asyncio.to_thread(mt5.positions_get)
        offset = await asyncio.to_thread(self.client.broker_utc_offset)
        return [position_from_raw(row, broker_utc_offset=offset) for row in _rows(rows)]

    async def open_orders(self, account_id: str) -> list[BrokerOrder]:
        return []

    async def mt5_orders(self) -> list[MT5Order]:
        mt5 = self.client.ensure_ready()
        rows = await asyncio.to_thread(mt5.orders_get)
        offset = await asyncio.to_thread(self.client.broker_utc_offset)
        return [order_from_raw(row, broker_utc_offset=offset) for row in _rows(rows)]

    async def history(self, days: int = 30) -> dict[str, list[MT5HistoryItem]]:
        mt5 = self.client.ensure_ready()
        to_date = datetime.now(timezone.utc)
        from_date = to_date - timedelta(days=days)
        deals = await asyncio.to_thread(mt5.history_deals_get, from_date, to_date)
        orders = await asyncio.to_thread(mt5.history_orders_get, from_date, to_date)
        offset = await asyncio.to_thread(self.client.broker_utc_offset)
        return {
            "deals": [history_item_from_raw(row, broker_utc_offset=offset) for row in _rows(deals)],
            "orders": [history_item_from_raw(row, broker_utc_offset=offset) for row in _rows(orders)],
        }

    async def history_orders(self, days: int = 30) -> list[MT5HistoryItem]:
        return (await self.history(days=days))["orders"]

    async def history_deals(self, days: int = 30) -> list[MT5HistoryItem]:
        return (await self.history(days=days))["deals"]

    async def scheduler_status(self) -> MT5SchedulerStatus:
        return MT5SchedulerStatus(
            max_ai_candidates_per_cycle=self.config.max_ai_candidates_per_cycle,
            order_submission_enabled=self.config.order_submission_enabled,
            live_trading_enabled=self.config.live_trading_enabled,
        )

    async def ai_usage_controls(self) -> dict[str, Any]:
        return {
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "max_ai_candidates_per_cycle": self.config.max_ai_candidates_per_cycle,
            "max_provider_requests_per_cycle": self.config.max_provider_requests_per_cycle,
            "max_provider_requests_per_hour": self.config.max_provider_requests_per_hour,
            "max_provider_requests_per_day": self.config.max_provider_requests_per_day,
            "daily_cost_limit_usd": self.config.daily_cost_limit_usd,
            "monthly_cost_limit_usd": self.config.monthly_cost_limit_usd,
            "target_max_input_tokens": self.config.target_max_input_tokens,
            "max_output_tokens": self.config.max_output_tokens,
            "api_key_exposed": False,
        }

    async def executions(self, account_id: str, since: datetime | None = None) -> list[BrokerExecution]:
        return []

    async def resolve_contract(self, instrument_id: str) -> BrokerContract:
        symbol = _symbol(instrument_id)
        info = await self.symbol_info(symbol)
        return BrokerContract(instrument_id=f"FX:{symbol}", symbol=symbol, asset_type="FOREX", exchange="MT5", currency=info.currency_profit or symbol[-3:], security_type="FOREX", con_id=abs(hash(symbol)) % 10_000_000, source="mt5", resolution_version="mt5.readonly.v1")

    async def symbol_spec(self, instrument_id: str) -> BrokerSymbolSpec:
        """Broker Independence Assessment Phase 1, item 3: real (not stubbed) implementation --
        low risk since this is a new read-only method nothing in production calls yet. Never
        guesses a missing field; raises rather than silently defaulting broker-reported metadata,
        matching this codebase's established convention (see e.g. families/_shared.py::
        _spread_within_safety_buffer's identical never-guessed posture for point_value)."""
        symbol = _symbol(instrument_id)
        info = await self.symbol_info(symbol)
        required = {"point": info.point, "digits": info.digits, "trade_contract_size": info.trade_contract_size, "volume_min": info.volume_min, "volume_max": info.volume_max, "volume_step": info.volume_step}
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise MT5UnavailableError(f"symbol_spec({symbol}): broker did not report required field(s): {missing}")
        # Standard MT4/MT5 EA-development convention: a pip is one order of magnitude larger than
        # a point for 3- and 5-digit symbols (fractional-pip quoting), and equal to a point
        # otherwise (2- and 4-digit symbols). Not independently re-derived per instrument type --
        # flagged in the migration report as worth validating against real live symbol data
        # before anything downstream relies on pip_size for money math.
        pip_size = info.point * 10 if info.digits in (3, 5) else info.point
        pip_position = (info.digits - 1) if info.digits in (3, 5) else info.digits
        return BrokerSymbolSpec(
            instrument_id=f"FX:{symbol}",
            broker="mt5",
            pip_position=pip_position,
            pip_size=pip_size,
            digits=info.digits,
            lot_size=info.trade_contract_size,
            volume_min=info.volume_min,
            volume_max=info.volume_max,
            volume_step=info.volume_step,
            contract_size=info.trade_contract_size,
            margin_currency=info.currency_margin or "USD",
            stops_level=Decimal(str(info.trade_stops_level)) if info.trade_stops_level is not None else None,
            freeze_level=Decimal(str(info.trade_freeze_level)) if info.trade_freeze_level is not None else None,
        )

    async def reconcile(self, account_id: str) -> BrokerReconciliationResult:
        return BrokerReconciliationResult(account_id=account_id, status="MATCHED_EMPTY")

    async def submit_order(self, command: BrokerOrderCommand) -> BrokerOrderReceipt:
        raise MT5ReadOnlyViolation()

    async def cancel_order(self, command: BrokerCancelCommand) -> BrokerCancelReceipt:
        raise MT5ReadOnlyViolation()

    async def modify_position(self, command: BrokerModifyPositionCommand) -> BrokerModifyReceipt:
        # Broker Independence Assessment Phase 1: additive Protocol conformance only -- mirrors
        # submit_order/cancel_order's existing posture exactly. Real SL/TP modification still
        # lives in adaptive_management/service.py::_build_mt5_request until Phase 3/4 rewires
        # that call site onto this method; this phase changes zero execution behavior.
        raise MT5ReadOnlyViolation()

    async def close_position(self, command: BrokerClosePositionCommand) -> BrokerCloseReceipt:
        raise MT5ReadOnlyViolation()


def _symbol(instrument_id: str) -> str:
    return str(instrument_id or "").upper().replace("FX:", "").replace("/", "")


def _bar_size_to_timeframe(bar_size: str) -> str:
    raw = str(bar_size or "").upper().replace(" ", "")
    mapping = {"1MIN": "M1", "1M": "M1", "5MINS": "M5", "5MIN": "M5", "5M": "M5", "15MINS": "M15", "15MIN": "M15", "15M": "M15", "1H": "H1", "60MIN": "H1", "4H": "H4", "1D": "D1", "DAILY": "D1"}
    return mapping.get(raw, raw if raw in {"M1", "M5", "M15", "H1", "H4", "D1"} else "M15")


def _duration_to_count(duration: str, default: int) -> int:
    raw = str(duration or "").upper().strip()
    for token in raw.split():
        if token.isdigit():
            return max(default, min(5000, int(token) * default))
    return default


def _rows(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value)


def _live_universe_symbols(symbols: list[MT5Symbol], trading_symbols: tuple[str, ...]) -> list[MT5Symbol]:
    allowlist = {symbol.upper().replace("/", "") for symbol in trading_symbols}
    if not allowlist:
        return symbols
    selected: dict[str, MT5Symbol] = {}
    for symbol in symbols:
        classification = classify_symbol(symbol)
        if classification.canonical_pair in allowlist and classification.canonical_pair not in selected:
            selected[classification.canonical_pair] = symbol
    return [selected[pair] for pair in trading_symbols if pair in selected]


def _row_dict(row: Any) -> dict[str, Any]:
    if hasattr(row, "_asdict"):
        return row._asdict()
    if isinstance(row, dict):
        return row
    if hasattr(row, "dtype") and getattr(row.dtype, "names", None):
        return {key: row[key] for key in row.dtype.names}
    return dict(row)


def _persist_mt5_candles(candles: list[MT5Candle], *, symbol: str) -> None:
    try:
        persist_candles(candles, broker_symbol=symbol, canonical_symbol=symbol.upper(), provider="MT5", dataset_policy="MT5_ONLY")
    except Exception as exc:
        logging.getLogger(__name__).warning("MT5 candle persistence failed: %s", exc.__class__.__name__)


mt5_adapter = MT5Adapter()
