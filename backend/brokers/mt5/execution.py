from __future__ import annotations

import asyncio
from decimal import Decimal, ROUND_FLOOR
from typing import Any

from backend.brokers.mt5.config import MT5Config
from backend.brokers.mt5.diagnostics import assert_demo_account
from backend.brokers.mt5.exceptions import MT5ReadOnlyViolation
from backend.brokers.mt5.models import MT5OrderCheckResult, MT5OrderSubmissionResult, MT5RiskSizing, MT5Symbol, MT5TradeIntent
from backend.portfolio_execution.service import execution_manager


SUCCESS_RETCODE_NAMES = {
    "TRADE_RETCODE_DONE",
    "TRADE_RETCODE_DONE_PARTIAL",
    "TRADE_RETCODE_PLACED",
}


class MT5ExecutionService:
    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter
        self.config: MT5Config = adapter.config
        self.order_send_calls = 0

    async def safety_blockers(self) -> list[str]:
        blockers: list[str] = []
        if not self.config.enabled:
            blockers.append("MT5_DISABLED")
        if self.config.account_mode != "DEMO":
            blockers.append("MT5_ACCOUNT_MODE_NOT_DEMO")
        if self.config.live_trading_enabled:
            blockers.append("LIVE_TRADING_ENABLED")
        if self.config.broker_provider != "MT5" or self.config.forex_execution_provider != "MT5":
            blockers.append("MT5_NOT_CONFIGURED_AS_FOREX_EXECUTION_PROVIDER")
        if not self.config.order_submission_enabled:
            blockers.append("MT5_ORDER_SUBMISSION_DISABLED")
        if not self.config.autonomous_submission_enabled:
            blockers.append("MT5_AUTONOMOUS_SUBMISSION_DISABLED")
        try:
            terminal = await self.adapter.terminal_status()
            account = await self.adapter.mt5_account()
            assert_demo_account(self.config, account.model_dump())
            if self.config.server and account.server != self.config.server:
                blockers.append("MT5_DEMO_SERVER_MISMATCH")
            if not terminal.connected:
                blockers.append("TERMINAL_DISCONNECTED")
            if terminal.trade_allowed is False or terminal.external_python_trading_allowed is False:
                blockers.append("TERMINAL_TRADING_NOT_ALLOWED")
            if terminal.account_mode != "DEMO":
                blockers.append("CRITICAL_LIVE_ACCOUNT_DETECTED")
        except Exception as exc:
            blockers.append(f"BROKER_NOT_READY:{exc.__class__.__name__}")
        return blockers

    async def calculate_risk_size(
        self,
        *,
        account_equity: Decimal,
        symbol: MT5Symbol,
        direction: str,
        entry: Decimal,
        stop: Decimal,
        target: Decimal,
    ) -> MT5RiskSizing:
        reasons = _geometry_reasons(direction, entry, stop, target)
        equity_risk_cap = (account_equity * Decimal(str(self.config.risk_percent_per_trade)) / Decimal("100")).quantize(Decimal("0.01"))
        trade_risk_cap = Decimal(str(self.config.max_risk_per_trade_usd))
        aggregate_percent_cap = account_equity * Decimal(str(self.config.max_total_open_risk_percent)) / Decimal("100")
        effective_risk = min(equity_risk_cap, trade_risk_cap, Decimal(str(self.config.max_total_open_risk_usd)), Decimal(str(self.config.max_daily_loss_usd)), aggregate_percent_cap)
        tick_size = symbol.trade_tick_size or symbol.point
        tick_value = symbol.trade_tick_value_loss or symbol.trade_tick_value or symbol.trade_tick_value_profit
        step = symbol.volume_step or Decimal("0.01")
        minimum = symbol.volume_min or Decimal("0.01")
        maximum = symbol.volume_max or Decimal("100")
        if not tick_size or tick_size <= 0 or not tick_value or tick_value <= 0:
            reasons.append("TICK_VALUE_UNAVAILABLE")
        if reasons:
            return MT5RiskSizing(status="REJECTED", reasons=reasons, equity_risk_cap_usd=equity_risk_cap, trade_risk_cap_usd=trade_risk_cap)
        stop_distance = abs(entry - stop)
        loss_per_lot = (stop_distance / tick_size) * tick_value
        if loss_per_lot <= 0:
            return MT5RiskSizing(status="REJECTED", reasons=["PROJECTED_LOSS_UNVERIFIABLE"], equity_risk_cap_usd=equity_risk_cap, trade_risk_cap_usd=trade_risk_cap)
        raw_volume = effective_risk / loss_per_lot
        capped = min(raw_volume, maximum)
        volume = _round_down(capped, step)
        if volume < minimum:
            return MT5RiskSizing(status="REJECTED", reasons=["VOLUME_BELOW_MINIMUM"], equity_risk_cap_usd=equity_risk_cap, trade_risk_cap_usd=trade_risk_cap)
        projected_loss = (loss_per_lot * volume).copy_abs().quantize(Decimal("0.01"))
        projected_profit = (abs(target - entry) / tick_size * tick_value * volume).copy_abs().quantize(Decimal("0.01"))
        rr = (projected_profit / projected_loss).quantize(Decimal("0.01")) if projected_loss > 0 else Decimal("0")
        if rr < Decimal("1.5"):
            reasons.append("RISK_REWARD_TOO_LOW")
        return MT5RiskSizing(
            status="APPROVED" if not reasons else "REJECTED",
            volume=volume,
            effective_risk_usd=effective_risk,
            projected_loss_usd=projected_loss,
            projected_profit_usd=projected_profit,
            risk_reward=rr,
            reasons=reasons,
            equity_risk_cap_usd=equity_risk_cap,
            trade_risk_cap_usd=trade_risk_cap,
        )

    async def order_calc_margin(self, intent: MT5TradeIntent) -> Decimal | None:
        mt5 = self.adapter.client.ensure_ready()
        side = intent.direction.upper()
        order_type = getattr(mt5, "ORDER_TYPE_BUY", 0) if side == "LONG" else getattr(mt5, "ORDER_TYPE_SELL", 1)
        if not hasattr(mt5, "order_calc_margin"):
            return None
        raw = await asyncio.to_thread(mt5.order_calc_margin, order_type, intent.broker_symbol, float(intent.volume), float(intent.entry_price))
        return _dec(raw)

    async def order_calc_stop_loss(self, intent: MT5TradeIntent) -> Decimal | None:
        mt5 = self.adapter.client.ensure_ready()
        side = intent.direction.upper()
        order_type = getattr(mt5, "ORDER_TYPE_BUY", 0) if side == "LONG" else getattr(mt5, "ORDER_TYPE_SELL", 1)
        if not hasattr(mt5, "order_calc_profit"):
            return None
        raw = await asyncio.to_thread(mt5.order_calc_profit, order_type, intent.broker_symbol, float(intent.volume), float(intent.entry_price), float(intent.stop_loss))
        return _dec(raw)

    async def order_check(self, intent: MT5TradeIntent) -> MT5OrderCheckResult:
        mt5 = self.adapter.client.ensure_ready()
        request = self._request(mt5, intent, filling_type=await self._filling_type(mt5, intent.broker_symbol))
        raw = await asyncio.to_thread(mt5.order_check, request)
        data = _asdict(raw)
        retcode = data.get("retcode")
        raw_payload = {
            "stage": "order_check",
            "request": _sanitize(request),
            "broker_response": _sanitize(data),
            "last_error": _last_error(mt5),
            "retcode_name": classify_retcode(mt5, retcode),
        }
        comment = data.get("comment") or ("empty order_check response" if not data else None)
        return MT5OrderCheckResult(ok=retcode == 0 or self._success_retcode(mt5, retcode), retcode=retcode, comment=comment, margin=_dec(data.get("margin")), raw=raw_payload)

    async def submit_market_order(self, intent: MT5TradeIntent, *, economic_context: dict[str, Any] | None = None) -> MT5OrderSubmissionResult:
        blockers = await self.safety_blockers()
        if blockers:
            raise MT5ReadOnlyViolation(";".join(blockers))
        mt5 = self.adapter.client.ensure_ready()
        filling_type = await self._filling_type(mt5, intent.broker_symbol)
        check = await self.order_check(intent)
        if not check.ok:
            return MT5OrderSubmissionResult(status=classify_retcode(mt5, check.retcode), retcode=check.retcode, comment=check.comment, requested_volume=intent.volume, request=check.raw.get("request") or {}, raw=check.raw)
        margin = await self.order_calc_margin(intent)
        stop_loss_projection = await self.order_calc_stop_loss(intent)
        if margin is None or stop_loss_projection is None:
            return MT5OrderSubmissionResult(status="REJECTED", comment="order_calc_margin/order_calc_profit unavailable", requested_volume=intent.volume, request={}, raw={"reason": "BROKER_CALC_UNAVAILABLE"})
        if stop_loss_projection >= 0:
            return MT5OrderSubmissionResult(status="REJECTED", comment="stop-loss projection is not a loss", requested_volume=intent.volume, request={}, raw={"projected_stop_loss": str(stop_loss_projection)})
        request = self._request(mt5, intent, filling_type=filling_type)
        self.order_send_calls += 1
        raw = await execution_manager.submit_mt5_request(
            adapter=self.adapter,
            request=request,
            idempotency_key=f"mt5-entry:{intent.intent_id}:{intent.context_hash}",
            source="mt5_autonomous_entry",
            expected_price=float(intent.entry_price),
            economic_context=economic_context,
        )
        data = _asdict(raw)
        retcode = data.get("retcode")
        status = str(data.get("status") or ("ACCEPTED" if self._success_retcode(mt5, retcode) else classify_retcode(mt5, retcode)))
        return MT5OrderSubmissionResult(
            status=status,
            retcode=retcode,
            comment=data.get("comment"),
            order_ticket=data.get("order"),
            deal_ticket=data.get("deal"),
            fill_price=_dec(data.get("price")),
            requested_volume=intent.volume,
            filled_volume=_dec(data.get("volume")),
            request=_sanitize(request),
            raw=_sanitize(data),
        )

    async def _filling_type(self, mt5: Any, symbol_name: str) -> int:
        try:
            symbol = await self.adapter.symbol_info(symbol_name)
            flags = int(symbol.filling_mode or 0)
        except Exception:
            flags = 0
        if flags & 1:
            return int(getattr(mt5, "ORDER_FILLING_FOK", 0))
        if flags & 2:
            return int(getattr(mt5, "ORDER_FILLING_IOC", 1))
        return int(getattr(mt5, "ORDER_FILLING_RETURN", getattr(mt5, "ORDER_FILLING_IOC", 1)))

    def _request(self, mt5: Any, intent: MT5TradeIntent, *, filling_type: int | None = None) -> dict[str, Any]:
        side = intent.direction.upper()
        order_type = getattr(mt5, "ORDER_TYPE_BUY", 0) if side == "LONG" else getattr(mt5, "ORDER_TYPE_SELL", 1)
        return {
            "action": getattr(mt5, "TRADE_ACTION_DEAL", 1),
            "symbol": intent.broker_symbol,
            "volume": float(intent.volume),
            "type": order_type,
            "price": float(intent.entry_price),
            "sl": float(intent.stop_loss),
            "tp": float(intent.take_profit),
            "deviation": intent.deviation,
            "magic": intent.magic,
            "comment": intent.comment,
            "type_time": getattr(mt5, "ORDER_TIME_GTC", 0),
            "type_filling": filling_type if filling_type is not None else getattr(mt5, "ORDER_FILLING_IOC", getattr(mt5, "ORDER_FILLING_FOK", 0)),
        }

    def _success_retcode(self, mt5: Any, retcode: Any) -> bool:
        if retcode is None:
            return False
        return retcode in {value for value in (getattr(mt5, name, None) for name in SUCCESS_RETCODE_NAMES) if value is not None}


def classify_retcode(mt5: Any, retcode: Any) -> str:
    mapping = {
        value: label
        for value, label in (
            (getattr(mt5, "TRADE_RETCODE_INVALID_VOLUME", None), "INVALID_VOLUME"),
            (getattr(mt5, "TRADE_RETCODE_INVALID_STOPS", None), "INVALID_STOPS"),
            (getattr(mt5, "TRADE_RETCODE_INVALID_FILL", None), "INVALID_FILL"),
            (getattr(mt5, "TRADE_RETCODE_NO_MONEY", None), "NO_MONEY"),
            (getattr(mt5, "TRADE_RETCODE_MARKET_CLOSED", None), "MARKET_CLOSED"),
            (getattr(mt5, "TRADE_RETCODE_PRICE_CHANGED", None), "PRICE_CHANGED"),
            (getattr(mt5, "TRADE_RETCODE_REQUOTE", None), "REQUOTE"),
            (getattr(mt5, "TRADE_RETCODE_TRADE_DISABLED", None), "TRADE_DISABLED"),
        )
        if value is not None
    }
    return mapping.get(retcode, "SUBMISSION_UNKNOWN" if retcode is None else "REJECTED")


def _geometry_reasons(direction: str, entry: Decimal, stop: Decimal, target: Decimal) -> list[str]:
    side = direction.upper()
    if side == "LONG" and not (stop < entry < target):
        return ["INVALID_SL_TP_GEOMETRY"]
    if side == "SHORT" and not (target < entry < stop):
        return ["INVALID_SL_TP_GEOMETRY"]
    if side not in {"LONG", "SHORT"}:
        return ["INVALID_DIRECTION"]
    return []


def _round_down(value: Decimal, step: Decimal) -> Decimal:
    units = (value / step).to_integral_value(rounding=ROUND_FLOOR)
    return (units * step).quantize(step)


def _asdict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "_asdict"):
        return value._asdict()
    if isinstance(value, dict):
        return value
    return dict(value)


def _sanitize(data: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in data.items():
        key_str = str(key)
        if any(part in key_str.lower() for part in ("password", "api_key", "apikey", "secret", "token", "authorization")):
            clean[key_str] = "***REDACTED***"
        elif isinstance(value, Decimal):
            clean[key_str] = str(value)
        elif isinstance(value, dict):
            clean[key_str] = _sanitize(value)
        else:
            clean[key_str] = value
    return clean


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _last_error(mt5: Any) -> tuple[Any, ...] | None:
    try:
        error = mt5.last_error()
        return tuple(error) if error is not None else None
    except Exception:
        return None
