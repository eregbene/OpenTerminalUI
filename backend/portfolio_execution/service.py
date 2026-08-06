from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Sequence

from backend.brokers.mt5 import account_registry
from backend.brokers.mt5.adapter import mt5_adapter
from backend.brokers.mt5.config import mt5_config
from backend.brokers.mt5.persistence import sanitize
from backend.portfolio_execution.orm import ExecutionMetricORM, ExecutionOrderORM, ExecutionStateTransitionORM, PortfolioSnapshotORM
from backend.shared.db import SessionLocal

logger = logging.getLogger(__name__)

SUCCESS_RETCODES = {"TRADE_RETCODE_DONE", "TRADE_RETCODE_DONE_PARTIAL", "TRADE_RETCODE_PLACED"}
REJECT_RETCODES = {"TRADE_RETCODE_REQUOTE", "TRADE_RETCODE_PRICE_CHANGED", "TRADE_RETCODE_INVALID_FILL"}


class PortfolioManager:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._cycle_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="portfolio-execution-monitor")
        logger.warning("Portfolio manager started")

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.warning("Portfolio manager stopped")

    async def _loop(self) -> None:
        interval = _env_int("PORTFOLIO_MANAGER_INTERVAL_SECONDS", 15, 5, 120)
        while not self._stop_event.is_set():
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Portfolio manager refresh failed: %s", exc.__class__.__name__)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def refresh(self) -> dict[str, Any]:
        if self._cycle_lock.locked():
            return self.status()
        async with self._cycle_lock:
            snapshot = await self.build_snapshot()
            with SessionLocal() as db:
                row = PortfolioSnapshotORM(snapshot_id=snapshot["snapshot_id"])
                for key, value in snapshot.items():
                    if hasattr(row, key):
                        setattr(row, key, sanitize(value))
                db.merge(row)
                db.commit()
            return snapshot

    async def build_snapshot(self) -> dict[str, Any]:
        account = await mt5_adapter.mt5_account()
        positions = await mt5_adapter.mt5_positions()
        history = await mt5_adapter.history(days=7)
        realized = sum(float(row.profit or 0) + float(row.commission or 0) for row in history.get("deals", []) if row.symbol)
        floating = sum(float(row.profit or 0) + float(row.swap or 0) for row in positions)
        equity = float(account.equity)
        balance = float(account.balance)
        margin = float(account.margin)
        free_margin = float(account.free_margin)
        quote_currencies = {_currencies(str(row.symbol or "").upper())[1] for row in positions}
        conversion_rates = {currency: await _fx_conversion_rate(currency) for currency in quote_currencies if currency and currency != "USD"}
        risk_rows = [_position_risk(row, conversion_rates.get(_currencies(str(row.symbol or "").upper())[1], 1.0)) for row in positions]
        exposure = _exposure(positions)
        correlation = await correlation_engine.matrix([row.symbol for row in positions])
        priorities = _position_priorities(positions, risk_rows)
        protection = self.protection_from_values(
            positions=positions,
            account={"equity": equity, "balance": balance, "margin": margin, "free_margin": free_margin},
            risk_rows=risk_rows,
            exposure=exposure,
        )
        return sanitize(
            {
                "snapshot_id": "PES_" + _hash({"account": account.login, "time": utcnow().isoformat()})[:32],
                "account_id": str(account.login),
                "broker": "MT5",
                "account_mode": mt5_config().account_mode,
                "balance": balance,
                "equity": equity,
                "free_margin": free_margin,
                "margin": margin,
                "margin_level": equity / margin * 100 if margin else None,
                "floating_pnl": floating,
                "realized_pnl": realized,
                "drawdown": max(0.0, balance - equity),
                "open_risk": sum(abs(row["stop_loss_projection"]) for row in risk_rows),
                "projected_stop_loss": sum(row["stop_loss_projection"] for row in risk_rows),
                "projected_take_profit": sum(row["take_profit_projection"] for row in risk_rows),
                "worst_case_loss": sum(min(0.0, row["stop_loss_projection"]) for row in risk_rows),
                "expected_gain": sum(max(0.0, row["take_profit_projection"]) for row in risk_rows),
                "margin_utilization": margin / equity if equity else 0.0,
                "var_estimate": _var_estimate(risk_rows),
                "maximum_simultaneous_loss": sum(abs(min(0.0, row["stop_loss_projection"])) for row in risk_rows),
                "exposure_by_currency": exposure["currency"],
                "exposure_by_symbol": exposure["symbol"],
                "exposure_by_strategy": exposure["strategy"],
                "exposure_by_direction": exposure["direction"],
                "exposure_by_timeframe": exposure["timeframe"],
                "exposure_by_session": exposure["session"],
                "correlation_matrix": correlation,
                "position_priority": priorities,
                "protection_state": protection,
                "raw_payload": {"positions": [row.model_dump(mode="json") for row in positions]},
            }
        )

    def status(self) -> dict[str, Any]:
        latest = self.latest_snapshot()
        return {"running": bool(self._task and not self._task.done()), "latest_snapshot": latest}

    def latest_snapshot(self) -> dict[str, Any] | None:
        with SessionLocal() as db:
            row = db.query(PortfolioSnapshotORM).order_by(PortfolioSnapshotORM.created_at.desc()).first()
            return _orm_dict(row) if row else None

    def exposure(self) -> dict[str, Any]:
        latest = self.latest_snapshot() or {}
        return {
            "currency": latest.get("exposure_by_currency") or {},
            "symbol": latest.get("exposure_by_symbol") or {},
            "strategy": latest.get("exposure_by_strategy") or {},
            "direction": latest.get("exposure_by_direction") or {},
            "timeframe": latest.get("exposure_by_timeframe") or {},
            "session": latest.get("exposure_by_session") or {},
        }

    def risk(self) -> dict[str, Any]:
        latest = self.latest_snapshot() or {}
        keys = ("open_risk", "projected_stop_loss", "projected_take_profit", "worst_case_loss", "expected_gain", "margin_utilization", "var_estimate", "maximum_simultaneous_loss", "protection_state", "position_priority")
        return {key: latest.get(key) for key in keys}

    def protection_from_values(self, *, positions: list[Any], account: dict[str, float], risk_rows: list[dict[str, float]], exposure: dict[str, Any]) -> dict[str, Any]:
        cfg = mt5_config()
        blockers: list[str] = []
        if cfg.live_trading_enabled:
            blockers.append("LIVE_TRADING_BLOCKED")
        if len(positions) >= cfg.max_open_positions:
            blockers.append("MAX_OPEN_POSITIONS")
        open_risk = sum(abs(min(0.0, row["stop_loss_projection"])) for row in risk_rows)
        if open_risk > cfg.max_total_open_risk_usd:
            blockers.append("MAX_TOTAL_OPEN_RISK")
        equity = account.get("equity") or 0
        margin = account.get("margin") or 0
        margin_utilization = margin / equity if equity else 0
        if margin_utilization > _env_float("PORTFOLIO_MAX_MARGIN_UTILIZATION", 0.50):
            blockers.append("MAX_MARGIN_UTILIZATION")
        max_currency = max((abs(row.get("net", 0)) for row in exposure["currency"].values()), default=0)
        if max_currency > _env_float("PORTFOLIO_MAX_CORRELATED_EXPOSURE", 1_000_000_000):
            blockers.append("MAX_CORRELATED_EXPOSURE")
        return {"new_entries_allowed": not blockers, "blockers": sorted(set(blockers)), "live_trading_enabled": cfg.live_trading_enabled}

    def can_open_new_trade(self) -> tuple[bool, list[str]]:
        latest = self.latest_snapshot()
        if not latest:
            return True, []
        protection = latest.get("protection_state") or {}
        blockers = list(protection.get("blockers") or [])
        return not blockers, blockers


class ExecutionManager:
    def status(self) -> dict[str, Any]:
        return {"broker": "MT5", "live_trading_enabled": mt5_config().live_trading_enabled, "metrics": self.metrics()}

    def orders(self) -> list[dict[str, Any]]:
        with SessionLocal() as db:
            return [_orm_dict(row) for row in db.query(ExecutionOrderORM).order_by(ExecutionOrderORM.created_at.desc()).limit(200).all()]

    def journal(self) -> dict[str, Any]:
        with SessionLocal() as db:
            orders = [_orm_dict(row) for row in db.query(ExecutionOrderORM).order_by(ExecutionOrderORM.created_at.desc()).limit(200).all()]
            transitions = [_orm_dict(row) for row in db.query(ExecutionStateTransitionORM).order_by(ExecutionStateTransitionORM.created_at.desc()).limit(500).all()]
        return {"orders": orders, "transitions": transitions}

    def metrics(self) -> dict[str, Any]:
        with SessionLocal() as db:
            row = db.query(ExecutionMetricORM).order_by(ExecutionMetricORM.created_at.desc()).first()
            return _orm_dict(row) if row else self._recompute_metrics()

    async def submit_mt5_request(self, *, adapter: Any, request: dict[str, Any], idempotency_key: str, source: str, expected_price: float | None = None, economic_context: dict[str, Any] | None = None) -> dict[str, Any]:
        cfg = mt5_config()
        if cfg.live_trading_enabled:
            return self._reject(idempotency_key, request, source, "LIVE_TRADING_BLOCKED", economic_context=economic_context)
        if adapter is not None:
            # Independent, always-active account-identity gate -- the one chokepoint every
            # order-submission caller (autonomous entries and Adaptive Trade Manager's direct
            # calls) shares, so switching MT5 accounts (e.g. onto the new 10K demo account)
            # can never silently inherit a previous account's approval or risk state.
            try:
                account = await adapter.mt5_account()
                account_blockers = account_registry.account_blockers(account, account_mode=cfg.account_mode)
            except Exception as exc:
                account_blockers = [f"ACCOUNT_REGISTRY_UNAVAILABLE:{exc.__class__.__name__}"]
            if account_blockers:
                return self._reject(idempotency_key, request, source, ";".join(account_blockers), economic_context=economic_context)
        allowed, blockers = portfolio_manager.can_open_new_trade() if source == "mt5_autonomous_entry" else (True, [])
        if not allowed:
            return self._reject(idempotency_key, request, source, ";".join(blockers), economic_context=economic_context)
        with SessionLocal() as db:
            existing = db.query(ExecutionOrderORM).filter(ExecutionOrderORM.idempotency_key == idempotency_key).first()
            if existing and existing.state in {"SUBMITTED", "ACCEPTED", "PARTIALLY_FILLED", "FILLED", "RECONCILED", "REJECTED"}:
                existing.duplicate = True
                db.merge(existing)
                db.commit()
                return {"status": existing.state, "duplicate": True, "raw": existing.raw_response, "retcode": existing.retcode}
            row = existing or ExecutionOrderORM(execution_id="EXE_" + _hash({"key": idempotency_key})[:40])
            row.idempotency_key = idempotency_key
            row.source = source
            row.broker = "MT5"
            row.symbol = str(request.get("symbol") or "").upper()
            row.action_type = _request_action(request)
            row.requested_volume = _float(request.get("volume"))
            row.raw_request = sanitize(request)
            row.economic_context = sanitize(economic_context) if economic_context is not None else row.economic_context
            row.state = "PENDING"
            db.merge(row)
            self._transition(db, row.execution_id, None, "PENDING", "normalized")
            db.commit()
        preflight = await self._preflight(adapter, request)
        if preflight:
            return self._reject(idempotency_key, request, source, preflight)
        mt5 = adapter.client.ensure_ready()
        start = time.perf_counter()
        with SessionLocal() as db:
            row = db.query(ExecutionOrderORM).filter(ExecutionOrderORM.idempotency_key == idempotency_key).first()
            if row:
                self._transition(db, row.execution_id, row.state, "SUBMITTED", "broker_request")
                row.state = "SUBMITTED"
                db.merge(row)
                db.commit()
        raw = await asyncio.to_thread(mt5.order_send, request)
        latency_ms = (time.perf_counter() - start) * 1000
        data = _asdict(raw)
        retcode = data.get("retcode")
        state = _state_from_retcode(mt5, retcode, data)
        with SessionLocal() as db:
            row = db.query(ExecutionOrderORM).filter(ExecutionOrderORM.idempotency_key == idempotency_key).first()
            if row:
                row.retcode = _int(retcode)
                row.state = state
                row.order_ticket = str(data.get("order") or "") or None
                row.deal_ticket = str(data.get("deal") or "") or None
                row.latency_ms = latency_ms
                row.fill_latency_ms = latency_ms if state in {"ACCEPTED", "FILLED", "PARTIALLY_FILLED"} else None
                row.slippage = _slippage(expected_price, data.get("price"))
                row.spread_paid = _float(request.get("spread_paid"))
                row.rejection_reason = None if state in {"ACCEPTED", "FILLED", "PARTIALLY_FILLED"} else str(data.get("comment") or state)
                row.raw_response = sanitize(data)
                self._transition(db, row.execution_id, "SUBMITTED", state, row.rejection_reason or "broker_response", data)
                db.merge(row)
                db.commit()
        self._recompute_metrics()
        return data | {"execution_state": state, "latency_ms": latency_ms}

    async def _preflight(self, adapter: Any, request: dict[str, Any]) -> str | None:
        try:
            terminal = await adapter.terminal_status()
            if terminal.account_mode != "DEMO":
                return "DEMO_ACCOUNT_REQUIRED"
            if terminal.trade_allowed is False or terminal.external_python_trading_allowed is False:
                return "TERMINAL_TRADING_NOT_ALLOWED"
            symbol = str(request.get("symbol") or "")
            info = await adapter.symbol_info(symbol)
            if info.trade_mode not in {None, 0, 1, 2, 3, 4}:
                return "SYMBOL_TRADE_DISABLED"
            volume = Decimal(str(request.get("volume") or "0"))
            if volume and info.volume_min and volume < info.volume_min:
                return "VOLUME_BELOW_MINIMUM"
            quote = await adapter.latest_tick(symbol)
            if quote.spread is not None and float(quote.spread) > _env_float("EXECUTION_MAX_SPREAD", 1000):
                return "SPREAD_TOO_WIDE"
        except Exception as exc:
            return f"BROKER_NOT_READY:{exc.__class__.__name__}"
        return None

    def _reject(self, idempotency_key: str, request: dict[str, Any], source: str, reason: str, *, economic_context: dict[str, Any] | None = None) -> dict[str, Any]:
        with SessionLocal() as db:
            row = db.query(ExecutionOrderORM).filter(ExecutionOrderORM.idempotency_key == idempotency_key).first() or ExecutionOrderORM(execution_id="EXE_" + _hash({"key": idempotency_key})[:40])
            row.idempotency_key = idempotency_key
            row.source = source
            row.broker = "MT5"
            row.symbol = str(request.get("symbol") or "").upper()
            row.action_type = _request_action(request)
            row.requested_volume = _float(request.get("volume"))
            row.state = "REJECTED"
            row.rejection_reason = reason
            row.raw_request = sanitize(request)
            row.raw_response = {"reason": reason}
            row.economic_context = sanitize(economic_context) if economic_context is not None else row.economic_context
            db.merge(row)
            self._transition(db, row.execution_id, None, "REJECTED", reason)
            db.commit()
        self._recompute_metrics()
        return {"status": "REJECTED", "execution_state": "REJECTED", "comment": reason, "retcode": None}

    def _transition(self, db: Any, execution_id: str, from_state: str | None, to_state: str, reason: str, payload: dict[str, Any] | None = None) -> None:
        row = ExecutionStateTransitionORM(transition_id="EXT_" + _hash({"execution": execution_id, "from": from_state, "to": to_state, "reason": reason, "time": utcnow().isoformat()})[:48])
        row.execution_id = execution_id
        row.from_state = from_state
        row.to_state = to_state
        row.reason = reason
        row.raw_payload = sanitize(payload or {})
        db.merge(row)

    def _recompute_metrics(self) -> dict[str, Any]:
        with SessionLocal() as db:
            rows = db.query(ExecutionOrderORM).order_by(ExecutionOrderORM.created_at.desc()).limit(100).all()
            latencies = [row.latency_ms for row in rows if row.latency_ms is not None]
            fills = [row.fill_latency_ms for row in rows if row.fill_latency_ms is not None]
            slippage = [row.slippage for row in rows if row.slippage is not None]
            spreads = [row.spread_paid for row in rows if row.spread_paid is not None]
            metric = {
                "metric_id": "EXM_" + _hash({"time": utcnow().replace(second=0, microsecond=0).isoformat()})[:32],
                "window": "rolling_100",
                "average_latency_ms": _avg(latencies),
                "fill_latency_ms": _avg(fills),
                "average_slippage": _avg(slippage),
                "average_spread_paid": _avg(spreads),
                "requotes": sum(1 for row in rows if "REQUOTE" in str(row.rejection_reason or "")),
                "rejections": sum(1 for row in rows if row.state == "REJECTED"),
                "partial_fills": sum(1 for row in rows if row.state == "PARTIALLY_FILLED"),
                "broker_errors": sum(1 for row in rows if row.state == "REJECTED" and row.retcode is not None),
                "total_orders": len(rows),
            }
            row = ExecutionMetricORM(metric_id=metric["metric_id"])
            for key, value in metric.items():
                setattr(row, key, value)
            row.raw_payload = {"source": "execution_orders"}
            db.merge(row)
            db.commit()
            return metric


class CorrelationEngine:
    async def matrix(self, symbols: list[str]) -> dict[str, Any]:
        unique = sorted({symbol.upper() for symbol in symbols if symbol})
        closes: dict[str, list[float]] = {}
        for symbol in unique:
            try:
                candles = await mt5_adapter.candles(symbol, "M15", count=60)
                closes[symbol] = [float(row.close) for row in candles if row.close]
            except Exception:
                closes[symbol] = []
        matrix: dict[str, dict[str, float | None]] = {}
        for left in unique:
            matrix[left] = {}
            for right in unique:
                matrix[left][right] = 1.0 if left == right else _corr(_returns(closes.get(left, [])), _returns(closes.get(right, [])))
        return {"timeframe": "M15", "symbols": unique, "matrix": matrix, "highly_correlated": _high_corr(matrix)}


def _position_risk(position: Any, usd_conversion_rate: float = 1.0) -> dict[str, float]:
    direction = "LONG" if int(position.type or 0) == 0 else "SHORT"
    entry = float(position.price_open or 0)
    price = float(position.price_current or entry)
    volume = float(position.volume or 0)
    sl = float(position.sl or entry)
    tp = float(position.tp or entry)
    contract = 100_000.0 if not str(position.symbol).upper().startswith("XAU") else 100.0
    signed = 1 if direction == "LONG" else -1
    rate = usd_conversion_rate or 1.0
    return {
        "symbol": position.symbol,
        "floating": float(position.profit or 0),
        "stop_loss_projection": (sl - entry) * volume * contract * signed * rate,
        "take_profit_projection": (tp - entry) * volume * contract * signed * rate,
        "current_projection": (price - entry) * volume * contract * signed * rate,
    }


async def _fx_conversion_rate(quote_currency: str) -> float:
    """USD value of 1 unit of `quote_currency`, needed because MT5's raw
    (price_diff * volume * contract) projection is denominated in the pair's quote
    currency, not account currency. 1.0 for USD (a no-op) and for any currency whose
    conversion pair can't be resolved (safe fallback rather than blocking on a missing rate)."""
    if not quote_currency or quote_currency == "USD":
        return 1.0
    try:
        direct = await mt5_adapter.latest_tick(f"{quote_currency}USD")
        if direct.bid and direct.ask:
            return float((direct.bid + direct.ask) / 2)
    except Exception:
        pass
    try:
        inverse = await mt5_adapter.latest_tick(f"USD{quote_currency}")
        if inverse.bid and inverse.ask:
            mid = float((inverse.bid + inverse.ask) / 2)
            if mid:
                return 1.0 / mid
    except Exception:
        pass
    logger.warning("FX conversion rate unavailable for quote currency %s; position risk for that pair may be inaccurate", quote_currency)
    return 1.0


def _exposure(positions: list[Any]) -> dict[str, Any]:
    exposure: dict[str, dict[str, Any]] = {"currency": {}, "symbol": {}, "strategy": {}, "direction": {}, "timeframe": {}, "session": {}}
    for pos in positions:
        symbol = str(pos.symbol or "").upper()
        volume = float(pos.volume or 0)
        direction = "LONG" if int(pos.type or 0) == 0 else "SHORT"
        signed = volume if direction == "LONG" else -volume
        base, quote = _currencies(symbol)
        _add_currency(exposure["currency"], base, signed)
        _add_currency(exposure["currency"], quote, -signed)
        _add_bucket(exposure["symbol"], symbol, signed)
        _add_bucket(exposure["direction"], direction, volume)
        _add_bucket(exposure["strategy"], _lineage(pos.comment, "strategy"), signed)
        _add_bucket(exposure["timeframe"], _lineage(pos.comment, "timeframe"), signed)
        _add_bucket(exposure["session"], _session(), signed)
    return exposure


def _add_currency(rows: dict[str, dict[str, float]], key: str, amount: float) -> None:
    row = rows.setdefault(key, {"gross": 0.0, "net": 0.0, "long": 0.0, "short": 0.0})
    row["gross"] += abs(amount)
    row["net"] += amount
    if amount >= 0:
        row["long"] += amount
    else:
        row["short"] += abs(amount)


def _add_bucket(rows: dict[str, dict[str, float]], key: str, amount: float) -> None:
    row = rows.setdefault(key or "UNKNOWN", {"gross": 0.0, "net": 0.0})
    row["gross"] += abs(amount)
    row["net"] += amount


def _position_priorities(positions: list[Any], risks: list[dict[str, float]]) -> list[dict[str, Any]]:
    by_symbol = {row["symbol"]: row for row in risks}
    ranked = []
    for pos in positions:
        risk = by_symbol.get(pos.symbol, {})
        score = abs(float(risk.get("stop_loss_projection") or 0)) + max(0, -float(pos.profit or 0))
        ranked.append({"ticket": pos.ticket, "symbol": pos.symbol, "priority_score": score, "reason": "risk_and_drawdown", "profit": float(pos.profit or 0)})
    return sorted(ranked, key=lambda row: row["priority_score"], reverse=True)


def _currencies(symbol: str) -> tuple[str, str]:
    if symbol.startswith("XAU"):
        return "XAU", symbol[3:6] or "USD"
    return symbol[:3], symbol[3:6]


def _lineage(comment: Any, key: str) -> str:
    text = str(comment or "")
    marker = f"{key}="
    if marker in text:
        return text.split(marker, 1)[1].split()[0].strip(",;")
    return "BENSIM_AUTO" if "BENSIM" in text.upper() else "UNKNOWN"


def _session() -> str:
    hour = utcnow().hour
    if 7 <= hour < 12:
        return "LONDON"
    if 12 <= hour < 21:
        return "NEW_YORK"
    return "ASIA"


def _returns(values: list[float]) -> list[float]:
    return [(values[idx] - values[idx - 1]) / values[idx - 1] for idx in range(1, len(values)) if values[idx - 1]]


def _corr(left: list[float], right: list[float]) -> float | None:
    size = min(len(left), len(right))
    if size < 5:
        return None
    a = left[-size:]
    b = right[-size:]
    ma = _avg(a)
    mb = _avg(b)
    denom = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / denom if denom else None


def _high_corr(matrix: dict[str, dict[str, float | None]]) -> list[dict[str, Any]]:
    rows = []
    for left, values in matrix.items():
        for right, value in values.items():
            if left < right and value is not None and abs(value) >= 0.8:
                rows.append({"left": left, "right": right, "correlation": value})
    return rows


def _var_estimate(rows: list[dict[str, float]]) -> float:
    losses = sorted(abs(min(0, row["stop_loss_projection"])) for row in rows)
    if not losses:
        return 0.0
    return losses[min(len(losses) - 1, int(len(losses) * 0.95))]


def _state_from_retcode(mt5: Any, retcode: Any, data: dict[str, Any]) -> str:
    success = {getattr(mt5, name, None) for name in SUCCESS_RETCODES}
    if retcode in success:
        requested = _float(((data.get("request") or {}) if isinstance(data.get("request"), dict) else {}).get("volume"))
        filled = _float(data.get("volume"))
        return "PARTIALLY_FILLED" if requested and filled and filled < requested else "ACCEPTED"
    return "REJECTED"


def _request_action(request: dict[str, Any]) -> str:
    if request.get("action") == 6:
        return "MODIFY_SL_TP"
    return "DEAL"


def _slippage(expected: float | None, actual: Any) -> float | None:
    if expected is None or actual is None:
        return None
    return float(actual) - float(expected)


def _avg(values: Sequence[float | None]) -> float:
    clean = [float(v) for v in values if v is not None]
    return sum(clean) / len(clean) if clean else 0.0


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def _asdict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "_asdict"):
        return value._asdict()
    return value if isinstance(value, dict) else dict(value)


def _orm_dict(row: Any) -> dict[str, Any]:
    return {column.name: sanitize(getattr(row, column.name)) for column in row.__table__.columns}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(sanitize(payload), sort_keys=True, default=str).encode("utf-8")).hexdigest()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


portfolio_manager = PortfolioManager()
execution_manager = ExecutionManager()
correlation_engine = CorrelationEngine()
