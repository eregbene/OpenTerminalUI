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
from backend.brokers.mt5.multi_account import adapter_for_account
from backend.brokers.mt5.persistence import sanitize
from backend.brokers.mt5.risk_calculator import calculate_canonical_loss_per_lot, record_mismatch_if_needed
from backend.brokers.mt5.trading_costs import compute_trade_costs
from backend.mt5_strategies import redis_layer
from backend.mt5_strategies.models import normalize_strategy_id
from backend.portfolio_execution.orm import ExecutionMetricORM, ExecutionOrderORM, ExecutionStateTransitionORM, PortfolioSnapshotORM
from backend.shared.db import SessionLocal

logger = logging.getLogger(__name__)

SUCCESS_RETCODES = {"TRADE_RETCODE_DONE", "TRADE_RETCODE_DONE_PARTIAL", "TRADE_RETCODE_PLACED"}
REJECT_RETCODES = {"TRADE_RETCODE_REQUOTE", "TRADE_RETCODE_PRICE_CHANGED", "TRADE_RETCODE_INVALID_FILL"}


class PortfolioManager:
    def __init__(self, adapter: Any | None = None, account_id: str = "demo_10k") -> None:
        # `_explicit_adapter` is stored, not resolved once, so `adapter`/`config` stay
        # late-binding PROPERTIES below -- required for demo_10k, whose tests monkeypatch the
        # module-level `mt5_adapter` global and expect this instance to see the patched value on
        # every call, not the value that existed at construction time (mirrors
        # AdaptiveManagementService's identical adapter/config property pattern).
        self._explicit_adapter = adapter
        self.account_id = account_id
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._cycle_lock = asyncio.Lock()

    @property
    def adapter(self) -> Any:
        return self._explicit_adapter or mt5_adapter

    @property
    def config(self) -> Any:
        # Only an EXPLICIT (per-account) adapter's config is used directly -- the demo_10k
        # default instance (no explicit adapter) always re-reads mt5_config() fresh, exactly
        # matching this class's behavior before account-scoping existed (every method here used
        # to call mt5_config() directly, with no adapter indirection at all). Falling back to the
        # global mt5_adapter singleton's OWN .config here instead would silently stop picking up
        # env var changes for the default account, since MT5Config is frozen and mt5_adapter is
        # constructed once at import time.
        if self._explicit_adapter is not None:
            return getattr(self._explicit_adapter, "config", None) or mt5_config()
        return mt5_config()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name=f"portfolio-execution-monitor:{self.account_id}")
        logger.warning("Portfolio manager started account_id=%s", self.account_id)

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
        logger.warning("Portfolio manager stopped account_id=%s", self.account_id)

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
        adapter = self.adapter
        account = await adapter.mt5_account()
        positions = await adapter.mt5_positions()
        history = await adapter.history(days=7)
        # Canonical gross/commission/swap/fee/net breakdown (backend/brokers/mt5/
        # trading_costs.py) -- previously this summed only profit+commission (no swap, no fee),
        # inconsistent with the 4-term formula used elsewhere in the codebase for the same deals.
        cost_breakdown = compute_trade_costs([row for row in history.get("deals", []) if row.symbol])
        realized = cost_breakdown.net_pnl
        floating = sum(float(row.profit or 0) + float(row.swap or 0) for row in positions)
        equity = float(account.equity)
        balance = float(account.balance)
        margin = float(account.margin)
        free_margin = float(account.free_margin)
        quote_currencies = {_currencies(str(row.symbol or "").upper())[1] for row in positions}
        conversion_rates = {currency: await _fx_conversion_rate(currency, adapter) for currency in quote_currencies if currency and currency != "USD"}
        # One canonical-risk-calculator-backed lookup per unique symbol (not per position) --
        # several open positions on the same symbol share the same broker metadata.
        account_fingerprint = None
        try:
            account_fingerprint = account_registry.fingerprint_account(account).fingerprint_hash
        except Exception:
            pass
        symbol_infos: dict[str, Any] = {}
        for symbol_name in {str(row.symbol or "").upper() for row in positions}:
            try:
                symbol_infos[symbol_name] = await adapter.symbol_info(symbol_name)
            except Exception:
                symbol_infos[symbol_name] = None
        mt5_client = None
        try:
            mt5_client = adapter.client.ensure_ready()
        except Exception:
            pass
        risk_rows = [
            await _position_risk(
                row,
                conversion_rates.get(_currencies(str(row.symbol or "").upper())[1], 1.0),
                symbol_info=symbol_infos.get(str(row.symbol or "").upper()),
                mt5_client=mt5_client,
                account_fingerprint=account_fingerprint,
                config=self.config,
            )
            for row in positions
        ]
        exposure = _exposure(positions)
        correlation = await correlation_engine.matrix([row.symbol for row in positions], adapter)
        priorities = _position_priorities(positions, risk_rows)
        protection = self.protection_from_values(
            positions=positions,
            account={"equity": equity, "balance": balance, "margin": margin, "free_margin": free_margin},
            risk_rows=risk_rows,
            exposure=exposure,
        )
        return sanitize(
            {
                "snapshot_id": "PES_" + _hash({"account": self.account_id, "login": account.login, "time": utcnow().isoformat()})[:32],
                # Canonical logical account key -- NOT the raw MT5 login. Snapshot lookup
                # (latest_snapshot/exposure/can_open_new_trade) filters on THIS column, and the
                # rest of the codebase (autonomous.py, candidate_evaluation.py) always queries by
                # this same profile id, never by login. See mt5_login below for the raw login,
                # kept as metadata only (Bug 1 fix).
                "account_id": self.account_id,
                "mt5_login": str(account.login),
                "broker": "MT5",
                "account_mode": self.config.account_mode,
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
        latest = self.latest_snapshot(self.account_id)
        return {"account_id": self.account_id, "running": bool(self._task and not self._task.done()), "latest_snapshot": latest}

    def latest_snapshot(self, account_id: str | None = None) -> dict[str, Any] | None:
        # Legacy no-arg callers (the original single-account /api/portfolio/* routes) default to
        # demo_10k rather than querying across every account -- an unfiltered query would return
        # whichever account happened to refresh most recently, silently mixing accounts (exactly
        # the class of bug this fix removes elsewhere).
        resolved_account_id = account_id or "demo_10k"
        with SessionLocal() as db:
            row = (
                db.query(PortfolioSnapshotORM)
                .filter(PortfolioSnapshotORM.account_id == resolved_account_id)
                .order_by(PortfolioSnapshotORM.created_at.desc())
                .first()
            )
            return _orm_dict(row) if row else None

    def exposure(self, account_id: str | None = None) -> dict[str, Any]:
        latest = self.latest_snapshot(account_id) or {}
        return {
            "currency": latest.get("exposure_by_currency") or {},
            "symbol": latest.get("exposure_by_symbol") or {},
            "strategy": latest.get("exposure_by_strategy") or {},
            "direction": latest.get("exposure_by_direction") or {},
            "timeframe": latest.get("exposure_by_timeframe") or {},
            "session": latest.get("exposure_by_session") or {},
        }

    def risk(self, account_id: str | None = None) -> dict[str, Any]:
        latest = self.latest_snapshot(account_id) or {}
        keys = ("open_risk", "projected_stop_loss", "projected_take_profit", "worst_case_loss", "expected_gain", "margin_utilization", "var_estimate", "maximum_simultaneous_loss", "protection_state", "position_priority")
        return {key: latest.get(key) for key in keys}

    def protection_from_values(self, *, positions: list[Any], account: dict[str, float], risk_rows: list[dict[str, float]], exposure: dict[str, Any]) -> dict[str, Any]:
        cfg = self.config
        blockers: list[str] = []
        if cfg.live_trading_enabled:
            blockers.append("LIVE_TRADING_BLOCKED")
        if len(positions) >= cfg.max_open_positions:
            blockers.append("MAX_OPEN_POSITIONS")
        equity = account.get("equity") or 0
        margin = account.get("margin") or 0
        open_risk = sum(abs(min(0.0, row["stop_loss_projection"])) for row in risk_rows)
        # Account-equity-scaled aggregate open-risk cap (Bug 2) -- the old
        # cfg.max_total_open_risk_usd was a flat dollar constant shared across every account
        # regardless of size, so a 100K account was capped at the same absolute figure as the
        # 10K account.
        aggregate_cap_usd = equity * cfg.max_total_open_risk_percent / 100.0
        if open_risk > aggregate_cap_usd:
            blockers.append("MAX_TOTAL_OPEN_RISK")
        margin_utilization = margin / equity if equity else 0
        if margin_utilization > _env_float("PORTFOLIO_MAX_MARGIN_UTILIZATION", 0.50):
            blockers.append("MAX_MARGIN_UTILIZATION")
        # Priority 5.5 fix: the OLD check compared raw LOT-signed currency exposure
        # (exposure["currency"], from _exposure() -- unit is lots, not dollars) against
        # PORTFOLIO_MAX_CORRELATED_EXPOSURE, which was never configured and defaults to
        # 1_000_000_000 -- a threshold no real account's lot exposure could ever reach, making
        # this blocker permanently dormant since it shipped. Fixed two ways: (1) the comparison
        # now uses real DOLLAR RISK per currency (via _currency_risk_exposure, reusing the same
        # broker-verified stop_loss_projection every other risk figure on this page already
        # uses) instead of raw lots, which have no fixed dollar meaning across symbols/accounts;
        # (2) the threshold is equity-scaled (PORTFOLIO_MAX_CORRELATED_EXPOSURE_PERCENT, mirroring
        # aggregate_cap_usd's own fix above) instead of a flat constant. The default (1.125%) is
        # derived, not guessed: it reuses the SAME 0.75 concentration ratio the soft risk-budget
        # scaler (MAX_CORRELATED_OPEN_RISK_PCT, autonomous.py::_risk_budget_adjustment) already
        # applies to this exact aggregate-open-risk cap for the identical concept -- 75% of
        # max_total_open_risk_percent (1.50% x 0.75 = 1.125%) -- so a single currency's net
        # dollar risk is never allowed to consume more than three-quarters of the account's
        # total risk budget, leaving room for genuinely diversified (different-currency)
        # concurrent positions without duplicating or loosening the aggregate cap itself.
        currency_risk = _currency_risk_exposure(positions, risk_rows)
        max_currency_risk_usd = max((abs(v) for v in currency_risk.values()), default=0.0)
        correlated_cap_usd = equity * _env_float("PORTFOLIO_MAX_CORRELATED_EXPOSURE_PERCENT", 1.125) / 100.0
        if max_currency_risk_usd > correlated_cap_usd:
            blockers.append("MAX_CORRELATED_EXPOSURE")
        return {"new_entries_allowed": not blockers, "blockers": sorted(set(blockers)), "live_trading_enabled": cfg.live_trading_enabled}

    def can_open_new_trade(self, account_id: str | None = None) -> tuple[bool, list[str]]:
        """Fail-CLOSED, not fail-open (Bug 1): a missing or stale snapshot means this account's
        real exposure/correlation/margin state is unknown right now, which is exactly when new
        risk must NOT be added -- silently allowing in that state is the bug this replaces.
        `PORTFOLIO_SNAPSHOT_MAX_AGE_SECONDS` bounds how long a snapshot stays trustworthy (default
        180s -- well beyond the ~15s refresh interval, so brief refresh jitter doesn't itself
        cause a false block, but a genuinely stalled/crashed refresh loop does)."""
        latest = self.latest_snapshot(account_id)
        if not latest:
            return False, ["PORTFOLIO_STATE_UNAVAILABLE"]
        created_at = latest.get("created_at")
        if created_at is not None:
            age_seconds = (utcnow() - _as_aware_utc(created_at)).total_seconds()
            max_age = _env_int("PORTFOLIO_SNAPSHOT_MAX_AGE_SECONDS", 180, 30, 3600)
            if age_seconds > max_age:
                return False, ["PORTFOLIO_STATE_STALE"]
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

    async def submit_mt5_request(self, *, adapter: Any, request: dict[str, Any], idempotency_key: str, source: str, expected_price: float | None = None, economic_context: dict[str, Any] | None = None, account_id: str | None = None) -> dict[str, Any]:
        cfg = getattr(adapter, "config", None) or mt5_config()
        account_context = account_id or str(request.get("account_id") or getattr(cfg, "account_id", "") or "demo_10k")
        explicit_account_context = bool(account_id or request.get("account_id") or getattr(adapter, "config", None))
        if cfg.live_trading_enabled or mt5_config().live_trading_enabled:
            return self._reject(idempotency_key, request, source, "LIVE_TRADING_BLOCKED", economic_context=economic_context, account_id=account_context)
        if adapter is not None:
            # Independent, always-active account-identity gate -- the one chokepoint every
            # order-submission caller (autonomous entries and Adaptive Trade Manager's direct
            # calls) shares, so switching MT5 accounts (e.g. onto the new 10K demo account)
            # can never silently inherit a previous account's approval or risk state.
            try:
                account = await adapter.mt5_account()
                account_blockers = account_registry.account_blockers(account, account_mode=cfg.account_mode)
                account_context = account_id or str(request.get("account_id") or getattr(cfg, "account_id", "") or account_context)
                profile = account_registry.profile_by_id(account_context)
                if explicit_account_context and profile is not None:
                    account_blockers.extend(account_registry.validate_profile_account(profile, account))
            except Exception as exc:
                account_blockers = [f"ACCOUNT_REGISTRY_UNAVAILABLE:{exc.__class__.__name__}"]
            if account_blockers:
                return self._reject(idempotency_key, request, source, ";".join(account_blockers), economic_context=economic_context, account_id=account_context)
        allowed, blockers = portfolio_manager.can_open_new_trade(account_context) if source == "mt5_autonomous_entry" else (True, [])
        if not allowed:
            return self._reject(idempotency_key, request, source, ";".join(blockers), economic_context=economic_context, account_id=account_context)
        # Part 8: Redis-only pre-check, strictly IN FRONT OF (never instead of) the DB
        # idempotency check immediately below. try_execution_lock fails OPEN on a Redis outage
        # (redis_available=False) -- proceed to the unchanged DB check, which alone is already
        # sufficient for correctness. It fails CLOSED only on genuine lock contention while
        # Redis IS reachable (proceed=False): a concurrent request for this EXACT idempotency
        # key is already in flight, so this one is rejected without even querying the DB.
        proceed, redis_available = await redis_layer.try_execution_lock(idempotency_key, account_id=account_context)
        if not proceed:
            with SessionLocal() as db:
                existing = _execution_order_query(db, account_context, idempotency_key).first()
                if existing and existing.state in {"SUBMITTED", "ACCEPTED", "PARTIALLY_FILLED", "FILLED", "RECONCILED", "REJECTED"}:
                    existing.duplicate = True
                    db.merge(existing)
                    db.commit()
                    return {"status": existing.state, "duplicate": True, "raw": existing.raw_response, "retcode": existing.retcode}
            return self._reject(idempotency_key, request, source, "EXECUTION_LOCK_CONTENDED", economic_context=economic_context, account_id=account_context)
        with SessionLocal() as db:
            existing = _execution_order_query(db, account_context, idempotency_key).first()
            if existing and existing.state in {"SUBMITTED", "ACCEPTED", "PARTIALLY_FILLED", "FILLED", "RECONCILED", "REJECTED"}:
                existing.duplicate = True
                db.merge(existing)
                db.commit()
                return {"status": existing.state, "duplicate": True, "raw": existing.raw_response, "retcode": existing.retcode}
            row = existing or ExecutionOrderORM(execution_id="EXE_" + _hash({"account": account_context, "key": idempotency_key})[:40])
            row.account_id = account_context
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
            self._transition(db, row.execution_id, None, "PENDING", "normalized", account_id=account_context)
            db.commit()
        preflight = await self._preflight(adapter, request)
        if preflight:
            return self._reject(idempotency_key, request, source, preflight, account_id=account_context)
        mutation_blocker = await self._pre_mutation_account_blocker(adapter, account_context) if explicit_account_context else None
        if mutation_blocker:
            return self._reject(idempotency_key, request, source, mutation_blocker, account_id=account_context)
        mt5 = adapter.client.ensure_ready()
        # Execution-quality analytics gap fix: spread_paid was persisted from request.get(
        # "spread_paid"), but request is the raw MT5 order_send payload (symbol/volume/price/sl/
        # tp) -- no caller ever populates that key, so the column was always NULL. Fetch the
        # FRESHEST broker tick immediately before submission (never a cached/historical one, and
        # never used to influence the order itself -- purely an execution-quality observation)
        # and compute the real bid/ask spread paid at that instant.
        spread_paid: float | None = None
        try:
            symbol_for_quote = str(request.get("symbol") or "").upper()
            if symbol_for_quote:
                fresh_tick = await adapter.latest_tick(symbol_for_quote)
                if fresh_tick.bid is not None and fresh_tick.ask is not None:
                    spread_paid = float(fresh_tick.ask) - float(fresh_tick.bid)
        except Exception as exc:
            logger.warning("spread_paid tick fetch failed for %s, leaving null rather than fabricating: %s", request.get("symbol"), exc.__class__.__name__)
        start = time.perf_counter()
        with SessionLocal() as db:
            row = _execution_order_query(db, account_context, idempotency_key).first()
            if row:
                self._transition(db, row.execution_id, row.state, "SUBMITTED", "broker_request", account_id=account_context)
                row.state = "SUBMITTED"
                db.merge(row)
                db.commit()
        raw = await asyncio.to_thread(mt5.order_send, request)
        latency_ms = (time.perf_counter() - start) * 1000
        data = _asdict(raw)
        retcode = data.get("retcode")
        state = _state_from_retcode(mt5, retcode, data)
        with SessionLocal() as db:
            row = _execution_order_query(db, account_context, idempotency_key).first()
            if row:
                row.retcode = _int(retcode)
                row.state = state
                row.order_ticket = str(data.get("order") or "") or None
                row.deal_ticket = str(data.get("deal") or "") or None
                row.latency_ms = latency_ms
                row.fill_latency_ms = latency_ms if state in {"ACCEPTED", "FILLED", "PARTIALLY_FILLED"} else None
                row.slippage = _slippage(expected_price, data.get("price"))
                row.spread_paid = spread_paid
                row.rejection_reason = None if state in {"ACCEPTED", "FILLED", "PARTIALLY_FILLED"} else str(data.get("comment") or state)
                row.raw_response = sanitize(data)
                self._transition(db, row.execution_id, "SUBMITTED", state, row.rejection_reason or "broker_response", data, account_id=account_context)
                db.merge(row)
                db.commit()
        self._recompute_metrics()
        return data | {"execution_state": state, "latency_ms": latency_ms}

    async def _pre_mutation_account_blocker(self, adapter: Any, account_id: str) -> str | None:
        try:
            account = await adapter.mt5_account()
            profile = account_registry.profile_by_id(account_id)
            blockers = account_registry.validate_profile_account(profile, account) if profile is not None else []
        except Exception as exc:
            return f"ACCOUNT_REGISTRY_UNAVAILABLE:{exc.__class__.__name__}"
        return ";".join(blockers) if blockers else None

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

    def _reject(self, idempotency_key: str, request: dict[str, Any], source: str, reason: str, *, economic_context: dict[str, Any] | None = None, account_id: str = "demo_10k") -> dict[str, Any]:
        with SessionLocal() as db:
            row = _execution_order_query(db, account_id, idempotency_key).first() or ExecutionOrderORM(execution_id="EXE_" + _hash({"account": account_id, "key": idempotency_key})[:40])
            row.account_id = account_id
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
            self._transition(db, row.execution_id, None, "REJECTED", reason, account_id=account_id)
            db.commit()
        self._recompute_metrics()
        return {"status": "REJECTED", "execution_state": "REJECTED", "comment": reason, "retcode": None}

    def _transition(self, db: Any, execution_id: str, from_state: str | None, to_state: str, reason: str, payload: dict[str, Any] | None = None, *, account_id: str = "demo_10k") -> None:
        row = ExecutionStateTransitionORM(transition_id="EXT_" + _hash({"execution": execution_id, "from": from_state, "to": to_state, "reason": reason, "time": utcnow().isoformat()})[:48])
        row.account_id = account_id
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
    async def matrix(self, symbols: list[str], adapter: Any | None = None) -> dict[str, Any]:
        # `adapter` (defaults to the global demo_10k adapter for backward compatibility) so a
        # 25K/50K/100K snapshot's correlation matrix is read from THAT account's own MT5
        # terminal/bridge, not always the 10K one -- candle data itself is broker/terminal-wide
        # rather than account-specific, but routing through the right adapter keeps this
        # consistent with every other per-account broker call in build_snapshot.
        client = adapter or mt5_adapter
        unique = sorted({symbol.upper() for symbol in symbols if symbol})
        closes: dict[str, list[float]] = {}
        for symbol in unique:
            try:
                candles = await client.candles(symbol, "M15", count=60)
                closes[symbol] = [float(row.close) for row in candles if row.close]
            except Exception:
                closes[symbol] = []
        matrix: dict[str, dict[str, float | None]] = {}
        for left in unique:
            matrix[left] = {}
            for right in unique:
                matrix[left][right] = 1.0 if left == right else _corr(_returns(closes.get(left, [])), _returns(closes.get(right, [])))
        return {"timeframe": "M15", "symbols": unique, "matrix": matrix, "highly_correlated": _high_corr(matrix)}


async def _position_risk(position: Any, usd_conversion_rate: float = 1.0, *, symbol_info: Any | None = None, mt5_client: Any | None = None, account_fingerprint: str | None = None, config: Any | None = None) -> dict[str, float]:
    """Portfolio-level per-position risk projection -- PART 3's highest-priority migration
    target. Previously hardcoded a guessed contract size (100_000, or 100 "for XAU") instead of
    reading the real broker-reported trade_contract_size, the exact class of bug (a magic number
    standing in for real symbol metadata) behind the XAUUSD 10x sizing incident, just guessed
    differently. Now derives a broker-verified money-per-point ratio from the SAME canonical
    calculator (backend/brokers/mt5/risk_calculator.py) every other MT5 monetary-risk call site
    uses, then applies it exactly as the original formula did (money_per_point * price_diff *
    volume * direction_sign * fx_rate) -- so this function's OUTPUT SHAPE and SIGN CONVENTION are
    unchanged (`build_snapshot`/`protection_from_values`/tests all keep working), only the
    magic-number contract size is gone. `symbol_info`/`mt5_client` are optional so a caller
    without live broker metadata (e.g. a unit test exercising just the FX-conversion math) still
    gets a usable, clearly-labeled degraded result instead of an exception."""
    direction = "LONG" if int(position.type or 0) == 0 else "SHORT"
    entry = Decimal(str(position.price_open or 0))
    price = Decimal(str(position.price_current or entry))
    volume = float(position.volume or 0)
    sl = Decimal(str(position.sl or entry))
    tp = Decimal(str(position.tp or entry))
    signed = 1 if direction == "LONG" else -1
    rate = usd_conversion_rate or 1.0

    money_per_point = await _money_per_point(direction=direction, entry=entry, sl=sl, tp=tp, symbol_info=symbol_info, mt5_client=mt5_client, account_fingerprint=account_fingerprint, symbol=str(position.symbol or ""), config=config)

    def _project(target_price: Decimal) -> float:
        return float(target_price - entry) * volume * money_per_point * signed * rate

    return {
        "symbol": position.symbol,
        "floating": float(position.profit or 0),
        "stop_loss_projection": _project(sl),
        "take_profit_projection": _project(tp),
        "current_projection": _project(price),
    }


async def _money_per_point(*, direction: str, entry: Decimal, sl: Decimal, tp: Decimal, symbol_info: Any | None, mt5_client: Any | None, account_fingerprint: str | None, symbol: str, config: Any | None = None) -> float:
    """Broker-verified $ value of a 1.0-price-unit move for 1.0 lot -- derived from the canonical
    calculator's selected (most conservative) loss-per-lot estimate divided by whichever probe
    distance (SL or TP, whichever is farther from entry) produced it, since a linear MT5
    instrument's money-per-point is the same regardless of which distance you measure it with.
    Falls back to the legacy hardcoded 100_000/100_(XAU) guess ONLY when no symbol metadata is
    available at all (e.g. a broker outage, or a caller with no live client) -- never as a first
    choice."""
    if symbol_info is None:
        return 100.0 if symbol.upper().startswith("XAU") else 100_000.0
    sl_distance = abs(sl - entry)
    tp_distance = abs(tp - entry)
    probe_distance = sl_distance if sl_distance >= tp_distance else tp_distance
    if probe_distance <= 0:
        point = getattr(symbol_info, "point", None) or Decimal("0.0001")
        probe_distance = point * 100
    probe_stop = entry - probe_distance if direction == "LONG" else entry + probe_distance
    cfg = config or mt5_config()
    canonical = await calculate_canonical_loss_per_lot(
        direction=direction,
        entry=entry,
        stop=probe_stop,
        symbol_info=symbol_info,
        mt5_client=mt5_client,
        warning_pct=cfg.risk_calculation_disagreement_pct,
        critical_pct=cfg.risk_calculation_critical_disagreement_pct,
    )
    record_mismatch_if_needed(canonical, symbol=symbol, account_fingerprint=account_fingerprint, context="position_risk_snapshot")
    if canonical.selected_loss_per_lot is None or probe_distance <= 0:
        return 100.0 if symbol.upper().startswith("XAU") else 100_000.0
    return float(canonical.selected_loss_per_lot / probe_distance)


async def _fx_conversion_rate(quote_currency: str, adapter: Any | None = None) -> float:
    """USD value of 1 unit of `quote_currency`, needed because MT5's raw
    (price_diff * volume * contract) projection is denominated in the pair's quote
    currency, not account currency. 1.0 for USD (a no-op) and for any currency whose
    conversion pair can't be resolved (safe fallback rather than blocking on a missing rate)."""
    client = adapter or mt5_adapter
    if not quote_currency or quote_currency == "USD":
        return 1.0
    try:
        direct = await client.latest_tick(f"{quote_currency}USD")
        if direct.bid and direct.ask:
            return float((direct.bid + direct.ask) / 2)
    except Exception:
        pass
    try:
        inverse = await client.latest_tick(f"USD{quote_currency}")
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
        _add_bucket(exposure["strategy"], normalize_strategy_id(_lineage(pos.comment, "strategy")), signed)
        _add_bucket(exposure["timeframe"], _lineage(pos.comment, "timeframe"), signed)
        _add_bucket(exposure["session"], _session(), signed)
    return exposure


def _currency_risk_exposure(positions: list[Any], risk_rows: list[dict[str, float]]) -> dict[str, float]:
    """Dollar-risk-weighted currency exposure -- same base/quote attribution convention as
    _exposure()'s currency bucket, but signed by real broker-verified dollar risk-at-stop
    (abs(stop_loss_projection)) instead of raw lot size. Positions and risk_rows are the SAME
    list, same order (both built from one positions-iteration in build_snapshot -- see that
    method's own risk_rows comprehension), zipped by index rather than by symbol so multiple
    positions on the same symbol are never accidentally collapsed into one risk figure."""
    totals: dict[str, float] = {}
    for pos, risk in zip(positions, risk_rows):
        symbol = str(pos.symbol or "").upper()
        direction = "LONG" if int(pos.type or 0) == 0 else "SHORT"
        risk_dollars = abs(float(risk.get("stop_loss_projection") or 0.0))
        signed = risk_dollars if direction == "LONG" else -risk_dollars
        base, quote = _currencies(symbol)
        totals[base] = totals.get(base, 0.0) + signed
        totals[quote] = totals.get(quote, 0.0) - signed
    return totals


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
    if text.startswith("BSM|"):
        parts = text.split("|")
        strategy = parts[1] if len(parts) > 1 and parts[1] else "BSI_V3"
        mapping = {"strategy": strategy, "strategy_version": "v3", "setup": "MT5_AUTONOMOUS_ENTRY", "timeframe": "M15"}
        return mapping.get(key, "UNKNOWN")
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


def _execution_order_query(db: Any, account_id: str, idempotency_key: str) -> Any:
    return db.query(ExecutionOrderORM).filter(ExecutionOrderORM.account_id == account_id, ExecutionOrderORM.idempotency_key == idempotency_key)


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


def _as_aware_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class PortfolioMultiAccountOrchestrator:
    """Fans the portfolio-protection snapshot loop across every ENABLED MT5 account profile --
    mirrors backend.adaptive_management.service.AdaptiveManagementMultiAccountOrchestrator
    exactly (same _enabled_profiles/_enabled_services shape, same demo_10k-reuses-default_service
    special case for backward compatibility with every existing route/test that imports
    portfolio_manager expecting a single PortfolioManager).

    Each PortfolioManager instance already owns its own independent asyncio.Task/polling loop
    (_loop), so this orchestrator only needs to start/stop one such task per enabled account, not
    coordinate a shared cycle. Read methods (latest_snapshot/exposure/risk/can_open_new_trade)
    already take an explicit account_id and query the shared, now-correctly-scoped DB table
    directly -- __getattr__ forwards them (and any other instance method) to default_service,
    since none of them depend on which instance they're called on."""

    def __init__(self, default_service: PortfolioManager | None = None) -> None:
        self.default_service = default_service or PortfolioManager()
        self._services: dict[str, PortfolioManager] = {"demo_10k": self.default_service}

    def __getattr__(self, name: str) -> Any:
        return getattr(self.default_service, name)

    def _enabled_profiles(self) -> list[account_registry.MT5AccountProfile]:
        return [profile for profile in account_registry.configured_profiles() if profile.enabled]

    def _enabled_services(self) -> dict[str, PortfolioManager]:
        services: dict[str, PortfolioManager] = {}
        for profile in self._enabled_profiles():
            if profile.account_id == "demo_10k":
                service = self.default_service
            else:
                service = self._services.get(profile.account_id)
                if service is None:
                    service = PortfolioManager(adapter_for_account(profile.account_id), profile.account_id)
                    self._services[profile.account_id] = service
            services[profile.account_id] = service
        return services

    async def start(self) -> None:
        services = self._enabled_services()
        if not services:
            logger.warning("Portfolio manager multi-account monitor not started: no enabled MT5 account profiles")
            return
        for service in services.values():
            await service.start()
        logger.warning("Portfolio manager multi-account monitor started accounts=%s", ",".join(services))

    async def stop(self) -> None:
        for service in self._services.values():
            await service.stop()
        logger.warning("Portfolio manager multi-account monitor stopped")

    async def refresh_account(self, account_id: str) -> dict[str, Any] | None:
        """Priority 5.5: forces an immediate, synchronous snapshot refresh for ONE account,
        outside the normal ~15s periodic cadence. Closes (does not eliminate -- see
        _cross_account_concurrent_exposure_blockers' own docstring for the residual, narrower
        race) the main practical source of cross-account concurrent-exposure staleness: account
        cycles run sequentially and awaited (MT5MultiAccountAutonomousOrchestrator.run_cycle),
        so calling this right after a successful order submission means the NEXT account's
        cycle -- which runs moments later, in the same event loop, before this one -- sees this
        account's just-opened position instead of a snapshot that is up to ~15s stale. Safe to
        call from any account's own cycle: PortfolioManager.refresh() is already idempotent and
        self-locked (_cycle_lock), so this can never race with that account's own background
        loop, only skip a redundant concurrent run of it (returns the in-flight status instead)."""
        service = self._enabled_services().get(account_id) or self._services.get(account_id)
        if service is None:
            return None
        return await service.refresh()

    def account_status(self, account_id: str) -> dict[str, Any] | None:
        service = self._enabled_services().get(account_id) or self._services.get(account_id)
        return service.status() if service else None

    def status(self) -> dict[str, Any]:
        services = self._enabled_services()
        accounts = {account_id: service.status() for account_id, service in services.items()}
        default_status = self.default_service.status()
        return {
            **default_status,
            "multi_account_enabled": True,
            "enabled_accounts": list(accounts),
            "accounts": accounts,
        }


portfolio_manager = PortfolioMultiAccountOrchestrator(PortfolioManager())
execution_manager = ExecutionManager()
correlation_engine = CorrelationEngine()
