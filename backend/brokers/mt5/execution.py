from __future__ import annotations

import asyncio
from decimal import Decimal, ROUND_FLOOR
from typing import Any

from backend.brokers.mt5.config import MT5Config
from backend.brokers.mt5.diagnostics import assert_demo_account
from backend.brokers.mt5.exceptions import MT5ReadOnlyViolation
from backend.brokers.mt5.models import MT5OrderCheckResult, MT5OrderSubmissionResult, MT5RiskSizing, MT5Symbol, MT5TradeIntent
from backend.brokers.mt5.risk_calculator import CRITICAL_MISMATCH, calculate_canonical_loss_per_lot, record_mismatch_if_needed
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
        if self.config.database_recovery_in_progress:
            # Set via DATABASE_RECOVERY_IN_PROGRESS after the 2026-08-07 incident
            # (see migration 0036_db_incident_boundary / the db_incidents table).
            # Blocks NEW entries only -- existing position management is untouched
            # by this flag, matching Part 14 of the recovery directive.
            blockers.append("DATABASE_RECOVERY_IN_PROGRESS")
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
        risk_budget_adjustment: dict[str, Any] | None = None,
        portfolio_available_risk_usd: Decimal | None = None,
        account_fingerprint: str | None = None,
    ) -> MT5RiskSizing:
        """PART 4's required sizing sequence, using the single canonical monetary-risk
        calculator (backend/brokers/mt5/risk_calculator.py) for every loss estimate -- no other
        module may compute MT5 monetary risk a different way (see that module's docstring for
        the incident this replaced: a lone trade_tick_value field 10x under-estimated XAUUSD
        risk). `portfolio_available_risk_usd`, when provided, additionally caps sizing to
        whatever open-risk headroom the portfolio actually has left (optional/backward
        compatible -- omitted callers get identical behavior to before this parameter existed)."""
        reasons = _geometry_reasons(direction, entry, stop, target)
        # 1. Configured monetary risk budget.
        equity_risk_cap = (account_equity * Decimal(str(self.config.risk_percent_per_trade)) / Decimal("100")).quantize(Decimal("0.01"))
        trade_risk_cap = Decimal(str(self.config.max_risk_per_trade_usd))
        aggregate_percent_cap = account_equity * Decimal(str(self.config.max_total_open_risk_percent)) / Decimal("100")
        cap_values = [equity_risk_cap, trade_risk_cap, Decimal(str(self.config.max_total_open_risk_usd)), Decimal(str(self.config.max_daily_loss_usd)), aggregate_percent_cap]
        if portfolio_available_risk_usd is not None:
            cap_values.append(max(Decimal("0"), portfolio_available_risk_usd))
        effective_risk = min(cap_values)
        # v2 effective-risk-budget scaling (drawdown/portfolio exposure/correlation/economic
        # risk/strategy confidence) -- optional and multiplicative only, so callers that don't
        # pass it get byte-identical behavior to before this parameter existed. See
        # backend/brokers/mt5/risk_budget.py::compute_risk_multiplier.
        risk_multiplier = 1.0
        if risk_budget_adjustment is not None:
            risk_multiplier = float(risk_budget_adjustment.get("multiplier", 1.0))
            effective_risk = (effective_risk * Decimal(str(risk_multiplier))).quantize(Decimal("0.01"))
        step = symbol.volume_step or Decimal("0.01")
        minimum = symbol.volume_min or Decimal("0.01")
        maximum = symbol.volume_max or Decimal("100")
        if reasons:
            return MT5RiskSizing(status="REJECTED", reasons=reasons, equity_risk_cap_usd=equity_risk_cap, trade_risk_cap_usd=trade_risk_cap)

        # 2. Canonical projected loss for 1.0 lot -- most-conservative-of-every-available-method.
        mt5_client = self._native_client()
        canonical = await calculate_canonical_loss_per_lot(
            direction=direction,
            entry=entry,
            stop=stop,
            symbol_info=symbol,
            mt5_client=mt5_client,
            warning_pct=self.config.risk_calculation_disagreement_pct,
            critical_pct=self.config.risk_calculation_critical_disagreement_pct,
        )
        record_mismatch_if_needed(canonical, symbol=symbol.symbol, account_fingerprint=account_fingerprint, context="new_entry_sizing")
        diagnostics = dict(
            risk_calculation_method=canonical.selected_method,
            risk_calculation_estimates={name: est.to_dict() for name, est in canonical.estimates.items()},
            risk_calculation_disagreement_pct=canonical.max_disagreement_pct,
            risk_calculation_warning_codes=list(canonical.warning_codes),
        )
        if canonical.selected_loss_per_lot is None or canonical.selected_loss_per_lot <= 0:
            reasons.append("PROJECTED_LOSS_UNVERIFIABLE")
            return MT5RiskSizing(status="REJECTED", reasons=reasons, equity_risk_cap_usd=equity_risk_cap, trade_risk_cap_usd=trade_risk_cap, **diagnostics)
        if canonical.blocked:
            # Critical broker-metadata disagreement (>= MT5_RISK_CALCULATION_CRITICAL_DISAGREEMENT_PCT)
            # blocks a NEW entry outright -- this is exactly the path that must catch a future
            # XAUUSD-shaped incident before any order is sized, let alone submitted. Existing
            # position MANAGEMENT (SL/TP modification, partial close) deliberately does NOT call
            # this method and is therefore never blocked by it -- see PART 2.
            reasons.append(canonical.block_reason or CRITICAL_MISMATCH)
            return MT5RiskSizing(status="REJECTED", reasons=reasons, equity_risk_cap_usd=equity_risk_cap, trade_risk_cap_usd=trade_risk_cap, **diagnostics)
        loss_per_lot = canonical.selected_loss_per_lot

        # 3-4. raw_volume = budget / one_lot_loss, FLOORED to volume_step. Never rounded up.
        raw_volume = effective_risk / loss_per_lot
        capped = min(raw_volume, maximum)
        volume = _round_down(capped, step)

        # 5-6. volume_min / volume_max bounds. If even volume_min's monetary risk exceeds the
        # budget, BLOCK -- never round up merely to satisfy volume_min (this is the exact
        # invariant the XAUUSD incident violated).
        if volume < minimum:
            minimum_risk = (loss_per_lot * minimum).copy_abs().quantize(Decimal("0.01"))
            reasons.append("VOLUME_BELOW_MINIMUM_RISK_TOO_HIGH" if minimum_risk > effective_risk else "VOLUME_BELOW_MINIMUM")
            return MT5RiskSizing(status="REJECTED", reasons=reasons, effective_risk_usd=effective_risk, equity_risk_cap_usd=equity_risk_cap, trade_risk_cap_usd=trade_risk_cap, **diagnostics)

        # 7. Recalculate projected monetary loss at the normalized (post-floor) volume. MT5 lot
        # economics are linear in volume (broker-confirmed: order_calc_profit(0.1 lot) ==
        # order_calc_profit(1.0 lot)/10 exactly), so this is the same selected-method figure
        # scaled to `volume` -- not a second broker round trip for no additional information.
        projected_loss = (loss_per_lot * volume).copy_abs().quantize(Decimal("0.01"))
        tick_size = symbol.trade_tick_size or symbol.point
        tick_value = symbol.trade_tick_value_loss or symbol.trade_tick_value or symbol.trade_tick_value_profit
        if tick_size and tick_size > 0 and tick_value and tick_value > 0:
            projected_profit = (abs(target - entry) / tick_size * tick_value * volume).copy_abs().quantize(Decimal("0.01"))
        else:
            projected_profit = Decimal("0.00")

        # 8-9. Compare final projected loss against every permitted cap; block outside rounding
        # tolerance rather than silently accepting an over-budget trade because of floor rounding.
        tolerance = Decimal("0.02")
        if projected_loss > effective_risk + tolerance:
            reasons.append("PROJECTED_LOSS_EXCEEDS_RISK_BUDGET")
        if projected_loss > trade_risk_cap + tolerance:
            reasons.append("PROJECTED_LOSS_EXCEEDS_HARD_CAP")
        if portfolio_available_risk_usd is not None and projected_loss > portfolio_available_risk_usd + tolerance:
            reasons.append("PROJECTED_LOSS_EXCEEDS_PORTFOLIO_AVAILABLE_RISK")
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
            risk_multiplier=Decimal(str(risk_multiplier)),
            reasons=reasons,
            equity_risk_cap_usd=equity_risk_cap,
            trade_risk_cap_usd=trade_risk_cap,
            **diagnostics,
        )

    def _native_client(self) -> Any | None:
        """Best-effort raw MT5 client for the canonical calculator's broker-native
        order_calc_profit method -- None (not an error) when the terminal isn't ready yet, so
        sizing still proceeds using the contract-size/tick-value methods alone."""
        try:
            return self.adapter.client.ensure_ready()
        except Exception:
            return None

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
