from __future__ import annotations

import asyncio
import logging
import os
import socket
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.brokers import broker_registry
from backend.brokers.errors import BrokerError
from backend.brokers.ibkr.configuration import ibkr_config
from backend.brokers.models import BrokerOrderCommand
from backend.forex_intelligence.instruments import normalize_forex_symbol
from backend.intelligence.trading.candles import TIMEFRAME_SECONDS
from backend.intelligence.trading.config import AITradingConfig, ai_trading_config
from backend.intelligence.trading.diagnostics import build_live_diagnostics, persist_latest_diagnostics
from backend.intelligence.trading.models import ShadowAnalysisResult, TradeDecision
from backend.intelligence.trading.persistence import get_cycle, get_state, save_cycle, set_state, utcnow
from backend.intelligence.trading.usage import usage_ledger

logger = logging.getLogger(__name__)


@dataclass
class ScreenedCandidate:
    symbol: str
    timeframe: str
    score: float
    context: dict[str, Any]
    context_hash: str
    reasons: list[str] = field(default_factory=list)


@dataclass
class AutoPaperState:
    emergency_disabled: bool = False
    unresolved_submission: bool = False
    scheduler_owner: str | None = None
    scheduler_running: bool = False
    scheduler_started_at: datetime | None = None
    next_cycle_time: datetime | None = None
    current_state: str = "idle"
    last_result: dict[str, Any] | None = None
    last_cycle_id: str | None = None
    last_cycle_at: datetime | None = None
    trades_today: int = 0
    trades_by_symbol_today: dict[str, int] = field(default_factory=dict)
    daily_pnl: float = 0.0
    cooldown_until: dict[str, datetime] = field(default_factory=dict)
    submission_count: int = 0
    cancel_count: int = 0
    acceptance_complete: bool = False
    last_trade: dict[str, Any] | None = None
    broker_connected: bool = False
    broker_api_ready: bool = False
    broker_account: str | None = None
    broker_account_verified_paper: bool = False
    broker_reconciliation: str | None = None
    broker_readiness_error: str | None = None
    broker_last_ready_at: datetime | None = None
    latest_market_context: dict[str, Any] | None = None
    latest_consensus: dict[str, Any] | None = None
    latest_ai_decision: dict[str, Any] | None = None
    current_broker_order: dict[str, Any] | None = None
    current_position: dict[str, Any] | None = None
    current_position_count: int = 0
    current_open_order_count: int = 0
    protective_orders: dict[str, Any] | None = None
    latest_trade_result: dict[str, Any] | None = None
    latest_post_trade_review: dict[str, Any] | None = None
    active_threshold_profile: str = "PRODUCTION_INSTITUTIONAL"
    latest_profile_rejection_reasons: list[str] = field(default_factory=list)
    validation_exit_deadline: datetime | None = None
    production_profile_restored: bool = True
    daily_lock_active: bool = False
    daily_lock_reason: str | None = None
    cooldown_expiry: datetime | None = None


class AIAutoPaperTradingService:
    def __init__(self, ai_service: Any, config: AITradingConfig | None = None) -> None:
        self.ai_service = ai_service
        self.config = config or ai_trading_config()
        self.state = AutoPaperState()
        self._lock = asyncio.Lock()
        self._scheduler_task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None

    async def start_scheduler(self, *, owner: str | None = None) -> bool:
        if not self.config.scheduler_enabled:
            logger.warning("AI Scheduler not started: disabled")
            return False
        if self._scheduler_task and not self._scheduler_task.done():
            logger.warning("AI Scheduler already running owner=%s", self.state.scheduler_owner)
            return True
        scheduler_owner = owner or os.getenv("AI_SCHEDULER_OWNER") or f"{socket.gethostname()}:{os.getpid()}"
        if not self._acquire_scheduler_lock(scheduler_owner):
            logger.warning("AI Scheduler not started: duplicate owner exists")
            return False
        self._stop_event = asyncio.Event()
        self.state.scheduler_owner = scheduler_owner
        self.state.scheduler_started_at = utcnow()
        self.state.next_cycle_time = _next_completed_run_time(self._execution_timeframes())
        self.state.current_state = "sleeping"
        self._scheduler_task = asyncio.create_task(self._scheduler_loop(scheduler_owner), name="ai-auto-paper-scheduler")
        logger.warning("AI Scheduler started")
        logger.warning("Scheduler owner: %s", scheduler_owner)
        logger.warning("Next execution: %s", self.state.next_cycle_time.isoformat() if self.state.next_cycle_time else "pending")
        return True

    async def stop_scheduler(self) -> None:
        if not self._scheduler_task:
            return
        logger.warning("AI Scheduler stopping")
        if self._stop_event:
            self._stop_event.set()
        self._scheduler_task.cancel()
        try:
            await self._scheduler_task
        except asyncio.CancelledError:
            pass
        self._scheduler_task = None
        self.state.current_state = "idle"
        self._release_scheduler_lock()
        logger.warning("AI Scheduler stopped")

    async def initialize_broker_readiness(self, *, timeout_seconds: float | None = None, retry_interval_seconds: float = 5.0) -> bool:
        timeout = timeout_seconds if timeout_seconds is not None else min(float(self.config.analysis_interval_seconds), 60.0)
        deadline = utcnow() + timedelta(seconds=timeout)
        logger.warning("Waiting for IBKR paper session readiness")
        while utcnow() <= deadline:
            ready = await self._refresh_broker_readiness(connect=True)
            if ready:
                logger.warning("IBKR API ready")
                logger.warning("Initial reconciliation complete: %s", self.state.broker_reconciliation)
                return True
            await asyncio.sleep(retry_interval_seconds)
        logger.warning("Scheduler paused: broker unavailable error=%s", self.state.broker_readiness_error)
        return False

    async def _scheduler_loop(self, owner: str) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            if not await self._wait_until_broker_ready():
                break
            next_run = _next_completed_run_time(self._execution_timeframes())
            self.state.next_cycle_time = next_run
            self.state.current_state = "sleeping"
            self._heartbeat_scheduler_lock(owner)
            logger.warning("Sleeping until next completed execution candle: %s", next_run.isoformat())
            sleep_for = max(0.0, (next_run - utcnow()).total_seconds())
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_for)
                break
            except asyncio.TimeoutError:
                pass

            if self._stop_event.is_set():
                break
            if not await self._wait_until_broker_ready():
                break
            self.state.current_state = "running"
            logger.warning("Cycle started: %s", _cycle_id())
            try:
                self._heartbeat_scheduler_lock(owner)
                execute = self._autonomous_submission_enabled()
                result = await self.run_cycle(execute=execute, owner=owner)
                self.state.last_result = result
                logger.warning("Cycle finished: %s status=%s provider_calls=%s", result.get("cycle_id"), result.get("status"), result.get("provider_calls"))
            except Exception as exc:
                self.state.last_result = {"status": "ERROR", "error": exc.__class__.__name__}
                logger.exception("AI Scheduler cycle failed")
            finally:
                self.state.current_state = "sleeping"

    async def _wait_until_broker_ready(self) -> bool:
        assert self._stop_event is not None
        first_wait = True
        while not self._stop_event.is_set():
            ready = await self._refresh_broker_readiness(connect=True)
            if ready:
                if self.state.current_state == "WAITING_FOR_BROKER":
                    logger.warning("Scheduler resumed: broker ready")
                return True
            self.state.current_state = "WAITING_FOR_BROKER"
            if first_wait:
                logger.warning("Scheduler paused: broker unavailable error=%s", self.state.broker_readiness_error)
                first_wait = False
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=min(30, self.config.analysis_interval_seconds))
                return False
            except asyncio.TimeoutError:
                continue
        return False

    async def run_cycle(self, *, execute: bool = False, owner: str = "local") -> dict[str, Any]:
        async with self._lock:
            self._sync_runtime_profile()
            self._assert_scheduler_owner(owner)
            cycle_id = _cycle_id()
            self.state.scheduler_running = True
            self.state.last_cycle_id = cycle_id
            self.state.last_cycle_at = utcnow()
            try:
                blockers = await self._global_blockers()
                if blockers:
                    usage_ledger.record_skip(len(self.config.symbol_allowlist))
                    payload = {"cycle_id": cycle_id, "status": "SKIPPED_NO_CANDIDATE", "provider_calls": 0, "blockers": blockers, "candidates": []}
                    self._persist_cycle(payload, symbol=self.config.symbol_allowlist[0] if self.config.symbol_allowlist else "NONE")
                    return payload

                candidates = await self.screen_candidates()
                if candidates:
                    first = candidates[0]
                    first_cycle_id = _candle_cycle_id(first.symbol, first.timeframe, first.context.get("data_timestamp"))
                    if get_cycle(first_cycle_id):
                        usage_ledger.record_skip(1)
                        payload = {"cycle_id": first_cycle_id, "status": "SKIPPED_DUPLICATE_CANDLE", "provider_calls": 0, "candidate": _candidate_row(first)}
                        self._persist_cycle(payload, candidate=first)
                        return payload
                eligible = [row for row in candidates if not row.reasons]
                if not eligible:
                    usage_ledger.record_skip(len(candidates))
                    cycle_id = _candle_cycle_id(candidates[0].symbol, candidates[0].timeframe, candidates[0].context.get("data_timestamp")) if candidates else cycle_id
                    self.state.last_cycle_id = cycle_id
                    payload = {"cycle_id": cycle_id, "status": "SKIPPED_NO_CANDIDATE", "provider_calls": 0, "candidates": [_candidate_row(row) for row in candidates]}
                    self._persist_cycle(payload, candidate=candidates[0] if candidates else None)
                    return payload

                best = sorted(eligible, key=lambda row: row.score, reverse=True)[0]
                candle_cycle_id = _candle_cycle_id(best.symbol, best.timeframe, best.context.get("data_timestamp"))
                cycle_id = candle_cycle_id
                self.state.last_cycle_id = cycle_id
                can_request, reasons = usage_ledger.can_request(self.config, best.context_hash)
                if not can_request:
                    usage_ledger.record_skip(len(candidates))
                    payload = {"cycle_id": cycle_id, "status": "SKIPPED_PROVIDER_LIMIT", "provider_calls": 0, "blockers": reasons, "candidates": [_candidate_row(row) for row in candidates]}
                    self._persist_cycle(payload, candidate=best)
                    return payload

                result = await self.ai_service.analyze(best.symbol, best.timeframe)
                submitted = None
                if execute:
                    submitted = await self.submit_if_eligible(result, best.context)
                payload = {
                    "cycle_id": cycle_id,
                    "status": result.status,
                    "provider_calls": 1,
                    "candidate": _candidate_row(best),
                    "decision_id": result.decision_id,
                    "decision": result.decision.decision if result.decision else result.status,
                    "submitted": _json_safe(submitted),
                }
                self._persist_cycle(payload, candidate=best)
                return payload
            finally:
                self.state.scheduler_running = False

    async def screen_candidates(self) -> list[ScreenedCandidate]:
        out: list[ScreenedCandidate] = []
        for symbol in self.config.symbol_allowlist:
            normalized = normalize_forex_symbol(symbol)
            for timeframe in self._execution_timeframes():
                context, status, reasons = await self.ai_service._build_context(normalized, timeframe)
                self.state.latest_market_context = context.get("market_context")
                self.state.latest_consensus = {
                    "strategy_consensus": context.get("strategy_consensus"),
                    "consensus_eligibility": context.get("consensus_eligibility"),
                    "active_threshold_profile": context.get("active_threshold_profile"),
                    "conflict_classification": context.get("conflict_classification"),
                }
                self.state.active_threshold_profile = str(context.get("active_threshold_profile") or self.config.threshold_profile().name)
                diagnostics = build_live_diagnostics(context, profile=context.get("threshold_profile") or self.config.threshold_profile().model_dump())
                persist_latest_diagnostics(diagnostics)
                context_hash = self.ai_service.context_hash(context)
                local_reasons = list(reasons)
                if status != "READY":
                    local_reasons.append(status)
                if (context.get("data_quality") or {}).get("status") not in {None, "VALID"}:
                    local_reasons.append("DATA_QUALITY_INVALID")
                if timeframe not in self.config.timeframe_allowlist:
                    local_reasons.append("TIMEFRAME_NOT_ALLOWLISTED")
                if context_hash in usage_ledger.context_hashes:
                    local_reasons.append("DUPLICATE_CONTEXT")
                local_reasons.extend([reason for reason in await self._position_capacity_blockers() if reason not in local_reasons])
                if self.state.cooldown_until.get(normalized, datetime.min.replace(tzinfo=timezone.utc)) > utcnow():
                    local_reasons.append("COOLDOWN")
                if self.state.trades_today >= self.config.max_trades_per_day:
                    local_reasons.append("DAILY_TRADE_LIMIT")
                if self._acceptance_entry_used():
                    local_reasons.append("ACCEPTANCE_ENTRY_USED")
                if self._symbol_entries_used(normalized) >= self.config.max_trades_per_symbol_per_day:
                    local_reasons.append("SYMBOL_DAILY_TRADE_LIMIT")
                local_reasons.extend([reason for reason in self._daily_validation_blockers() if reason not in local_reasons])
                signal = (context.get("enabled_strategy_signals") or [{}])[0]
                score = float(signal.get("signal_strength") or 0)
                if not signal.get("entry_conditions_met"):
                    local_reasons.append("NO_DETERMINISTIC_ENTRY")
                    local_reasons.extend([reason for reason in (signal.get("eligibility_reasons") or []) if reason not in local_reasons])
                self.state.latest_profile_rejection_reasons = list(local_reasons)
                out.append(ScreenedCandidate(symbol=normalized, timeframe=timeframe, score=score, context=context, context_hash=context_hash, reasons=local_reasons))
        return out

    async def submit_if_eligible(self, result: ShadowAnalysisResult, context: dict[str, Any]) -> dict[str, Any] | None:
        decision = result.decision
        if not decision or decision.decision not in {"LONG", "SHORT"}:
            return None
        blockers = await self._submission_blockers(decision, context)
        if blockers:
            self._record_daily_rejection(blockers)
            return {"status": "REJECTED_AUTONOMOUS_GATE", "reasons": blockers}
        sizing = self._sizing_details(decision, context)
        quantity = Decimal(str(sizing["final_quantity"]))
        max_loss = float(sizing["estimated_total_loss"])
        if quantity <= 0:
            self._record_daily_rejection(["NO_SAFE_QUANTITY"])
            return {"status": "REJECTED_RISK", "reasons": ["NO_SAFE_QUANTITY"]}
        adapter = broker_registry.get("ibkr")
        account_id = await self._paper_account_id(adapter)
        intent_id = f"aiintent_{result.decision_id}"
        order_ref = f"AI_AUTO_PAPER_{result.decision_id}"
        self._persist_intent(intent_id, order_ref, result, context, quantity, max_loss, sizing=sizing)
        command = BrokerOrderCommand(
            canonical_order_id=intent_id,
            account_id=account_id,
            instrument_id=f"FX:{decision.symbol}",
            side="BUY" if decision.decision == "LONG" else "SELL",
            order_type="MARKET",
            time_in_force="DAY",
            quantity=quantity,
            approved_quantity=quantity,
            risk_evaluation_id=f"airisk_{result.decision_id}",
            idempotency_key=order_ref,
            correlation_id=order_ref,
            user_approval=False,
        )
        funding = None
        if hasattr(adapter, "validate_ai_auto_paper_funding_plan") and decision.proposed_entry is not None and decision.stop_loss is not None and decision.take_profit is not None:
            try:
                funding = await adapter.validate_ai_auto_paper_funding_plan(
                    command,
                    entry_price=Decimal(str(decision.proposed_entry)),
                    stop_price=Decimal(str(decision.stop_loss)),
                    take_profit=Decimal(str(decision.take_profit)),
                )
            except BrokerError as exc:
                details = getattr(exc, "details", {})
                if exc.code == "INSUFFICIENT_SETTLEMENT_CURRENCY" and _fx_insufficient_cash_policy() == "REDUCE":
                    reduced_quantity = _reduced_funding_quantity(details)
                    minimum_quantity = _fx_min_order_quantity()
                    if Decimal("0") < reduced_quantity < quantity and reduced_quantity >= minimum_quantity:
                        original_quantity = quantity
                        quantity = reduced_quantity
                        max_loss = float(Decimal(str(max_loss)) * (quantity / original_quantity))
                        command = command.model_copy(update={"quantity": quantity, "approved_quantity": quantity})
                        sizing = {
                            **sizing,
                            "final_quantity": str(quantity.quantize(Decimal("1"), rounding=ROUND_FLOOR)),
                            "estimated_total_loss": max_loss,
                            "funding_adjustment": {
                                "policy": "REDUCE",
                                "original_quantity": str(original_quantity),
                                "reduced_quantity": str(quantity),
                                "details": _json_safe(details),
                            },
                        }
                        self._persist_intent(intent_id, order_ref, result, context, quantity, max_loss, sizing=sizing)
                        try:
                            funding = await adapter.validate_ai_auto_paper_funding_plan(
                                command,
                                entry_price=Decimal(str(decision.proposed_entry)),
                                stop_price=Decimal(str(decision.stop_loss)),
                                take_profit=Decimal(str(decision.take_profit)),
                            )
                        except BrokerError as reduced_exc:
                            reduced_details = getattr(reduced_exc, "details", {})
                            self._record_daily_rejection([reduced_exc.code])
                            rejected = {
                                "status": "REJECTED_PRE_BROKER",
                                "reason": reduced_exc.code,
                                "details": _json_safe(
                                    {
                                        **(reduced_details if isinstance(reduced_details, dict) else {"details": reduced_details}),
                                        "reduction_policy": "REDUCE",
                                        "reduction_status": "REVALIDATION_FAILED",
                                    }
                                ),
                                "place_order_calls": 0,
                            }
                            self.state.latest_trade_result = rejected
                            return rejected
                    else:
                        details = {
                            **(details if isinstance(details, dict) else {"details": details}),
                            "reduction_policy": "REDUCE",
                            "reduction_status": "UNAVAILABLE",
                            "minimum_quantity": str(minimum_quantity),
                            "maximum_affordable_quantity": str(reduced_quantity),
                        }
                        self._record_daily_rejection([exc.code])
                        rejected = {
                            "status": "REJECTED_PRE_BROKER",
                            "reason": exc.code,
                            "details": _json_safe(details),
                            "place_order_calls": 0,
                        }
                        self.state.latest_trade_result = rejected
                        return rejected
                else:
                    self._record_daily_rejection([exc.code])
                    rejected = {
                        "status": "REJECTED_PRE_BROKER",
                        "reason": exc.code,
                        "details": _json_safe(details),
                        "place_order_calls": 0,
                    }
                    self.state.latest_trade_result = rejected
                    return rejected
        try:
            receipt = await self._guarded_submit(adapter, command)
        except BrokerError as exc:
            if exc.code in {"INSUFFICIENT_SETTLEMENT_CURRENCY", "FX_ACCOUNT_CAPABILITY_UNKNOWN"}:
                details = getattr(exc, "details", {})
                self._record_daily_rejection([exc.code])
                rejected = {
                    "status": "REJECTED_PRE_BROKER",
                    "reason": exc.code,
                    "details": _json_safe(details),
                    "place_order_calls": 0,
                }
                self.state.latest_trade_result = rejected
                return rejected
            raise
        self.state.submission_count += 1
        self._mark_acceptance_entry_used(decision.symbol)
        self.state.current_broker_order = _broker_order_summary(receipt)
        self.state.latest_ai_decision = result.model_dump(mode="json")
        protective = await self._submit_protective_orders(adapter, command, decision, receipt)
        self._arm_validation_exit_if_needed()
        self.state.trades_today += 1
        self.state.trades_by_symbol_today[decision.symbol] = self.state.trades_by_symbol_today.get(decision.symbol, 0) + 1
        cooldown_seconds = self.config.post_trade_cooldown_minutes * 60 if self.config.multi_trade_validation_mode else self.config.cooldown_seconds
        cooldown_until = utcnow() + timedelta(seconds=cooldown_seconds)
        self.state.cooldown_until[decision.symbol] = cooldown_until
        self.state.cooldown_expiry = cooldown_until
        self.state.last_trade = {
            "intent_id": intent_id,
            "order_reference": order_ref,
            "quantity": str(quantity),
            "maximum_loss": max_loss,
            "funding": funding,
            "receipt": receipt,
            "protective_orders": protective,
        }
        self.state.protective_orders = protective
        self.state.latest_trade_result = {"status": "ENTRY_SUBMITTED", "symbol": decision.symbol, "quantity": str(quantity), "submitted_at": utcnow().isoformat()}
        self._record_daily_submission(filled_quantity=_filled_quantity(receipt))
        return {"status": "SUBMITTED", **self.state.last_trade}

    async def emergency_disable(self) -> dict[str, Any]:
        self.state.emergency_disabled = True
        return {"emergency_disabled": True, "order_submission_blocked": True}

    async def emergency_enable(self) -> dict[str, Any]:
        blockers = await self._global_blockers(ignore_emergency=True)
        if blockers or self.state.unresolved_submission:
            return {"emergency_disabled": True, "enabled": False, "blockers": blockers + (["UNRESOLVED_SUBMISSION"] if self.state.unresolved_submission else [])}
        self.state.emergency_disabled = False
        return {"emergency_disabled": False, "enabled": True}

    def usage(self) -> dict[str, Any]:
        return usage_ledger.status(self.config, self.scheduler_status())

    def scheduler_status(self) -> dict[str, Any]:
        self._sync_runtime_profile()
        return {
            "enabled": self.config.scheduler_enabled,
            "owner": self.state.scheduler_owner,
            "running": self.state.scheduler_running,
            "scheduler_running": bool(self._scheduler_task and not self._scheduler_task.done()),
            "scheduler_started_at": self.state.scheduler_started_at.isoformat() if self.state.scheduler_started_at else None,
            "next_cycle_time": self.state.next_cycle_time.isoformat() if self.state.next_cycle_time else None,
            "current_cycle_id": self.state.last_cycle_id,
            "current_state": self.state.current_state,
            "last_result": self.state.last_result,
            "last_cycle_id": self.state.last_cycle_id,
            "last_cycle_at": self.state.last_cycle_at.isoformat() if self.state.last_cycle_at else None,
            "emergency_disabled": self.state.emergency_disabled,
            "autonomous_submission_enabled": self._autonomous_submission_enabled(),
            "active_profile": self.state.active_threshold_profile,
            "active_threshold_profile": self.state.active_threshold_profile,
            "validation_mode_enabled": self._validation_mode_enabled(),
            "production_thresholds": self.config.production_profile.model_dump(),
            "validation_thresholds": self.config.validation_profile.model_dump(),
            "latest_profile_rejection_reasons": self.state.latest_profile_rejection_reasons,
            "acceptance_complete": self.state.acceptance_complete or self.config.autonomous_acceptance_complete,
            "acceptance_entry_used": self._acceptance_entry_used(),
            "acceptance_remaining_entries": self._entries_remaining(),
            "daily_validation": self._daily_status(),
            "daily_entry_cap": self._daily_entry_cap(),
            "entries_used": int(self._daily_state().get("entries_submitted") or 0),
            "entries_remaining": self._entries_remaining(),
            "entries_by_symbol": dict(self._daily_state().get("entries_by_symbol") or {}),
            "cooldown_active": self._cooldown_active(),
            "cooldown_expiry": self.state.cooldown_expiry.isoformat() if self.state.cooldown_expiry else None,
            "consecutive_losses": int(self._daily_state().get("consecutive_losses") or 0),
            "daily_net_pnl": float(self._daily_state().get("daily_net_pnl") or 0.0),
            "daily_drawdown": float(self._daily_state().get("daily_maximum_drawdown") or 0.0),
            "canonical_paper_account": self._paper_account_status(),
            "total_open_risk": self._committed_open_risk(),
            "total_open_risk_cap": self.config.max_total_open_risk_usd,
            "open_risk_allowance_remaining": self._open_risk_allowance_remaining(),
            "daily_lock_active": self._daily_lock_active(),
            "daily_lock_reason": self._daily_state().get("daily_lock_reason"),
            "acceptance_armed": self._autonomous_submission_enabled() and not self._acceptance_entry_used(),
            "latest_market_context": self.state.latest_market_context,
            "latest_consensus": self.state.latest_consensus,
            "latest_ai_decision": self.state.latest_ai_decision,
            "current_broker_order": self.state.current_broker_order,
            "current_position": self.state.current_position,
            "current_position_count": self.state.current_position_count,
            "current_open_order_count": self.state.current_open_order_count,
            "max_open_positions": self.config.max_open_positions,
            "max_open_orders": self.config.max_open_orders,
            "protective_orders": self.state.protective_orders,
            "latest_trade_result": self.state.latest_trade_result,
            "latest_post_trade_review": self.state.latest_post_trade_review,
            "exit_deadline": self.state.validation_exit_deadline.isoformat() if self.state.validation_exit_deadline else None,
            "production_profile_restored": self.state.production_profile_restored,
            "broker": self._broker_status(),
        }

    def scheduler_api_status(self) -> dict[str, Any]:
        self._sync_runtime_profile()
        usage = usage_ledger.status(self.config, self.scheduler_status())
        return {
            "scheduler_running": bool(self._scheduler_task and not self._scheduler_task.done()),
            "scheduler_owner": self.state.scheduler_owner,
            "scheduler_started_at": self.state.scheduler_started_at.isoformat() if self.state.scheduler_started_at else None,
            "last_cycle_time": self.state.last_cycle_at.isoformat() if self.state.last_cycle_at else None,
            "next_cycle_time": self.state.next_cycle_time.isoformat() if self.state.next_cycle_time else None,
            "current_cycle_id": self.state.last_cycle_id,
            "current_state": self.state.current_state,
            "last_result": self.state.last_result,
            "provider_calls_today": usage["requests_today"],
            "estimated_cost_today": usage["estimated_cost_today"],
            "emergency_disable": self.state.emergency_disabled,
            "autonomous_submission_enabled": self._autonomous_submission_enabled(),
            "active_threshold_profile": self.state.active_threshold_profile,
            "validation_mode_enabled": self._validation_mode_enabled(),
            "production_thresholds": self.config.production_profile.model_dump(),
            "validation_thresholds": self.config.validation_profile.model_dump(),
            "latest_profile_rejection_reasons": self.state.latest_profile_rejection_reasons,
            "acceptance_armed": self._autonomous_submission_enabled() and not self._acceptance_entry_used(),
            "acceptance_remaining_entries": self._entries_remaining(),
            "active_profile": self.state.active_threshold_profile,
            "trading_date": self._daily_state().get("trading_date"),
            "daily_entry_cap": self._daily_entry_cap(),
            "entries_used": int(self._daily_state().get("entries_submitted") or 0),
            "entries_remaining": self._entries_remaining(),
            "entries_by_symbol": dict(self._daily_state().get("entries_by_symbol") or {}),
            "entries_filled": int(self._daily_state().get("entries_filled") or 0),
            "trades_closed": int(self._daily_state().get("trades_closed") or 0),
            "cooldown_active": self._cooldown_active(),
            "cooldown_expiry": self.state.cooldown_expiry.isoformat() if self.state.cooldown_expiry else None,
            "consecutive_losses": int(self._daily_state().get("consecutive_losses") or 0),
            "daily_gross_pnl": float(self._daily_state().get("daily_gross_pnl") or 0.0),
            "daily_net_pnl": float(self._daily_state().get("daily_net_pnl") or 0.0),
            "daily_drawdown": float(self._daily_state().get("daily_maximum_drawdown") or 0.0),
            "daily_loss_allowance_remaining": self._daily_loss_allowance_remaining(),
            "canonical_paper_account": self._paper_account_status(),
            "total_open_risk": self._committed_open_risk(),
            "total_open_risk_cap": self.config.max_total_open_risk_usd,
            "open_risk_allowance_remaining": self._open_risk_allowance_remaining(),
            "daily_lock_active": self._daily_lock_active(),
            "daily_lock_reason": self._daily_state().get("daily_lock_reason"),
            "daily_validation": self._daily_status(),
            "latest_market_context": self.state.latest_market_context,
            "latest_consensus": self.state.latest_consensus,
            "latest_ai_decision": self.state.latest_ai_decision,
            "current_broker_order": self.state.current_broker_order,
            "current_position": self.state.current_position,
            "current_position_count": self.state.current_position_count,
            "current_open_order_count": self.state.current_open_order_count,
            "max_open_positions": self.config.max_open_positions,
            "max_open_orders": self.config.max_open_orders,
            "protective_orders": self.state.protective_orders,
            "latest_trade_result": self.state.latest_trade_result,
            "latest_post_trade_review": self.state.latest_post_trade_review,
            "exit_deadline": self.state.validation_exit_deadline.isoformat() if self.state.validation_exit_deadline else None,
            "production_profile_restored": self.state.production_profile_restored,
            **self._broker_status(),
        }

    async def _guarded_submit(self, adapter: Any, command: BrokerOrderCommand) -> dict[str, Any]:
        if command.correlation_id is None or not command.correlation_id.startswith("AI_AUTO_PAPER_"):
            raise RuntimeError("AI_AUTO_PAPER_ORDER_REF_REQUIRED")
        if self.state.submission_count >= 1 and self.config.max_trades_per_day == 1:
            raise RuntimeError("FIRST_ACCEPTANCE_LIMIT_REACHED")
        if self._acceptance_entry_used():
            raise RuntimeError("ACCEPTANCE_ENTRY_USED")
        if hasattr(adapter, "submit_ai_auto_paper_order"):
            return await adapter.submit_ai_auto_paper_order(command)
        receipt = await adapter.submit_order(command)
        return receipt.model_dump(mode="json")

    async def _submit_protective_orders(self, adapter: Any, command: BrokerOrderCommand, decision: TradeDecision, receipt: dict[str, Any]) -> dict[str, Any]:
        filled_quantity = _filled_quantity(receipt)
        if filled_quantity <= 0:
            return {"status": "PENDING_ON_FILL", "reason": "ENTRY_NOT_FILLED_YET"}
        if decision.stop_loss is None or decision.take_profit is None:
            self.state.emergency_disabled = True
            self.state.unresolved_submission = True
            return {"status": "FAILED", "reason": "MISSING_STOP_OR_TARGET"}
        if not hasattr(adapter, "submit_ai_auto_paper_protective_orders"):
            self.state.emergency_disabled = True
            self.state.unresolved_submission = True
            return {"status": "FAILED", "reason": "PROTECTIVE_ORDER_SUPPORT_MISSING"}
        try:
            return await adapter.submit_ai_auto_paper_protective_orders(
                command,
                parent_receipt=receipt,
                stop_price=Decimal(str(decision.stop_loss)),
                take_profit=Decimal(str(decision.take_profit)),
                quantity=filled_quantity,
            )
        except Exception as exc:
            self.state.emergency_disabled = True
            self.state.unresolved_submission = True
            return {"status": "FAILED", "reason": _broker_error_code(exc)}

    def _sizing_details(self, decision: TradeDecision, context: dict[str, Any]) -> dict[str, Any]:
        if decision.proposed_entry is None or decision.stop_loss is None:
            return self._empty_sizing_details()
        stop_distance_decimal = abs(Decimal(str(decision.proposed_entry)) - Decimal(str(decision.stop_loss)))
        stop_distance = float(stop_distance_decimal)
        if stop_distance <= 0:
            return self._empty_sizing_details()
        equity = self._canonical_equity(context)
        percent_risk_cap = equity * (self.config.max_risk_percent / 100)
        daily_remaining = self._daily_loss_allowance_remaining()
        open_risk_remaining = self._open_risk_allowance_remaining()
        risk_cap = Decimal(str(min(self.config.max_trade_loss_usd, percent_risk_cap, daily_remaining, open_risk_remaining)))
        per_unit_cost = Decimal(str(self._estimated_per_unit_cost(decision.symbol)))
        per_unit_risk = stop_distance_decimal + per_unit_cost
        if risk_cap <= 0 or per_unit_risk <= 0:
            return self._empty_sizing_details()
        raw_quantity = (Decimal(str(min(self.config.max_trade_loss_usd, percent_risk_cap, daily_remaining, open_risk_remaining))) / stop_distance_decimal).quantize(Decimal("1"), rounding=ROUND_FLOOR)
        cost_adjusted_quantity = (risk_cap / per_unit_risk).quantize(Decimal("1"), rounding=ROUND_FLOOR)
        quantity = cost_adjusted_quantity
        quantity = min(quantity, Decimal(str(self.config.max_position_size_forex)))
        estimated_price_risk = quantity * stop_distance_decimal
        estimated_transaction_costs = quantity * per_unit_cost
        maximum_loss_decimal = quantity * per_unit_risk
        maximum_loss = float(maximum_loss_decimal)
        if (
            maximum_loss > self.config.max_trade_loss_usd
            or maximum_loss > self.config.max_total_open_risk_usd
            or quantity > Decimal(str(self.config.max_position_size_forex))
        ):
            quantity = Decimal("0")
        return {
            "entry": float(decision.proposed_entry),
            "stop": float(decision.stop_loss),
            "target": float(decision.take_profit) if decision.take_profit is not None else None,
            "stop_distance_pips": stop_distance / (0.01 if str(decision.symbol).upper().endswith("JPY") else 0.0001),
            "raw_quantity": str(raw_quantity),
            "cost_adjusted_quantity": str(cost_adjusted_quantity),
            "final_quantity": str(quantity.quantize(Decimal("1"), rounding=ROUND_FLOOR)),
            "estimated_price_risk": float(estimated_price_risk),
            "estimated_transaction_costs": float(estimated_transaction_costs),
            "estimated_total_loss": maximum_loss,
            "remaining_daily_allowance": daily_remaining,
            "remaining_open_risk_allowance": open_risk_remaining,
            "risk_cap": float(risk_cap),
            "risk_source": "min(equity_percent,trade_loss,daily_allowance,open_risk_allowance)",
        }

    def _empty_sizing_details(self) -> dict[str, Any]:
        return {
            "entry": None,
            "stop": None,
            "target": None,
            "stop_distance_pips": 0.0,
            "raw_quantity": "0",
            "cost_adjusted_quantity": "0",
            "final_quantity": "0",
            "estimated_price_risk": 0.0,
            "estimated_transaction_costs": 0.0,
            "estimated_total_loss": 0.0,
            "remaining_daily_allowance": self._daily_loss_allowance_remaining(),
            "remaining_open_risk_allowance": self._open_risk_allowance_remaining(),
            "risk_cap": 0.0,
            "risk_source": "unavailable",
        }

    def _size(self, decision: TradeDecision, context: dict[str, Any]) -> tuple[Decimal, float]:
        sizing = self._sizing_details(decision, context)
        return Decimal(str(sizing["final_quantity"])), float(sizing["estimated_total_loss"])

    def _canonical_equity(self, context: dict[str, Any]) -> float:
        account = context.get("account") or {}
        return float(account.get("equity") or account.get("canonical_equity") or self.config.paper_account_starting_balance)

    def _paper_account_status(self) -> dict[str, Any]:
        return {
            "configured_testing_balance": self.config.paper_account_starting_balance,
            "canonical_balance": self.config.paper_account_starting_balance,
            "canonical_equity": self.config.paper_account_starting_balance,
            "currency": self.config.paper_account_currency,
            "source": "PAPER_ACCOUNT_STARTING_BALANCE",
            "profile": "SYNTHETIC PAPER SCALE TEST",
        }

    def _estimated_per_unit_cost(self, symbol: str) -> float:
        if str(symbol).upper().endswith("JPY"):
            pip_size = 0.01
        else:
            pip_size = 0.0001
        price_cost = (self.config.estimated_slippage_pips + self.config.estimated_spread_pips) * pip_size
        commission_cost = max(0.0, self.config.estimated_commission_usd) / max(1, self.config.max_position_size_forex)
        return price_cost + commission_cost

    def _committed_open_risk(self) -> float:
        return float(self.state.current_position_count) * float(self.config.max_trade_loss_usd)

    def _open_risk_allowance_remaining(self) -> float:
        return max(0.0, float(self.config.max_total_open_risk_usd) - self._committed_open_risk())

    async def _submission_blockers(self, decision: TradeDecision, context: dict[str, Any]) -> list[str]:
        blockers = await self._global_blockers()
        if self.config.trading_mode != "AUTO_PAPER":
            blockers.append("AUTO_PAPER_MODE_REQUIRED")
        if not self._autonomous_submission_enabled():
            blockers.append("AI_ORDER_SUBMISSION_DISABLED")
        if self.config.autonomous_acceptance_complete or self.state.acceptance_complete:
            blockers.append("AUTONOMOUS_ACCEPTANCE_COMPLETE")
        if self._acceptance_entry_used():
            blockers.append("ACCEPTANCE_ENTRY_USED")
        blockers.extend([reason for reason in self._daily_validation_blockers() if reason not in blockers])
        profile = self.config.threshold_profile()
        if decision.confidence < profile.min_confidence:
            blockers.append("CONFIDENCE_TOO_LOW")
        if decision.risk_reward_ratio < profile.min_risk_reward:
            blockers.append("RISK_REWARD_TOO_LOW")
        if decision.entry_type != "MARKET":
            blockers.append("MARKET_ORDER_REQUIRED")
        if self.config.require_protective_orders and not hasattr(broker_registry.get("ibkr"), "submit_ai_auto_paper_protective_orders"):
            blockers.append("PROTECTIVE_ORDER_SUPPORT_MISSING")
        signal = (context.get("enabled_strategy_signals") or [{}])[0]
        direction = str(signal.get("signal_direction") or "").upper()
        if not signal.get("entry_conditions_met"):
            blockers.append("NO_DETERMINISTIC_ENTRY")
        eligibility = context.get("consensus_eligibility") or {}
        if eligibility and not bool(eligibility.get("eligible")):
            blockers.extend([reason for reason in eligibility.get("reasons", []) if reason not in blockers])
        if decision.decision == "LONG" and direction not in {"LONG", "BULLISH", "BUY"}:
            blockers.append("DETERMINISTIC_SIGNAL_DISAGREES")
        if decision.decision == "SHORT" and direction not in {"SHORT", "BEARISH", "SELL"}:
            blockers.append("DETERMINISTIC_SIGNAL_DISAGREES")
        if self.config.multi_trade_validation_mode and self._symbol_entries_used(decision.symbol) >= self.config.max_trades_per_symbol_per_day:
            blockers.append("SYMBOL_DAILY_TRADE_LIMIT")
        proposed_risk = 0.0
        if self._open_risk_allowance_remaining() <= 0:
            blockers.append("TOTAL_OPEN_RISK_LIMIT")
        if decision.proposed_entry is not None and decision.stop_loss is not None:
            _, proposed_risk = self._size(decision, context)
        if self.config.multi_trade_validation_mode and proposed_risk > self._daily_loss_allowance_remaining():
            blockers.append("AGGREGATE_DAILY_RISK_LIMIT")
        if proposed_risk > self._open_risk_allowance_remaining():
            blockers.append("TOTAL_OPEN_RISK_LIMIT")
        return blockers

    async def _global_blockers(self, *, ignore_emergency: bool = False) -> list[str]:
        blockers: list[str] = []
        if self.state.emergency_disabled and not ignore_emergency:
            blockers.append("EMERGENCY_DISABLED")
        if bool(getattr(ibkr_config, "live_trading_enabled", False)) or bool(getattr(ibkr_config, "allow_live", False)):
            blockers.append("LIVE_TRADING_ENABLED")
        if ibkr_config.mode != "PAPER":
            blockers.append("IBKR_PAPER_REQUIRED")
        if self.state.unresolved_submission:
            blockers.append("UNRESOLVED_SUBMISSION")
        if self.state.daily_pnl <= -abs(self.config.max_daily_loss_usd):
            blockers.append("DAILY_LOSS_LIMIT")
        blockers.extend([reason for reason in self._daily_validation_blockers() if reason not in blockers])
        blockers.extend(self._validation_mode_blockers())
        if not await self._refresh_broker_readiness(connect=False):
            blockers.append(f"BROKER_NOT_READY:{self.state.broker_readiness_error or 'UNKNOWN'}")
        else:
            if self.config.acceptance_validation_mode and not self.state.broker_account_verified_paper:
                blockers.append("VALIDATION_REQUIRES_VERIFIED_PAPER_ACCOUNT")
            try:
                adapter = broker_registry.get("ibkr")
                account_id = self.state.broker_account or await self._paper_account_id(adapter)
                orders = await adapter.open_orders(account_id)
                positions = await adapter.positions(account_id)
                open_positions = _open_fx_positions(positions)
                self.state.current_position = open_positions[0] if open_positions else None
                self.state.current_position_count = len(open_positions)
                self.state.current_open_order_count = len(orders)
                if len(orders) >= self.config.max_open_orders:
                    blockers.append("OPEN_ORDER_LIMIT")
                if len(open_positions) >= self.config.max_open_positions:
                    blockers.append("OPEN_POSITION_LIMIT")
                if self._non_allowlisted_open_fx_symbols(open_positions):
                    blockers.append("NON_ALLOWLISTED_FX_POSITION_OPEN")
            except Exception as exc:
                blockers.append(f"BROKER_NOT_READY:{_broker_error_code(exc)}")
        return blockers

    def _validation_mode_blockers(self) -> list[str]:
        if not self._validation_mode_enabled():
            self.state.active_threshold_profile = self.config.production_profile.name
            self.state.production_profile_restored = True
            return []
        self.state.active_threshold_profile = self.config.validation_profile.name
        self.state.production_profile_restored = False
        blockers: list[str] = []
        if self.config.trading_mode != "AUTO_PAPER":
            blockers.append("VALIDATION_REQUIRES_AUTO_PAPER")
        if ibkr_config.mode != "PAPER":
            blockers.append("VALIDATION_REQUIRES_IBKR_PAPER")
        if bool(getattr(ibkr_config, "live_trading_enabled", False)) or bool(getattr(ibkr_config, "allow_live", False)):
            blockers.append("VALIDATION_LIVE_TRADING_BLOCKED")
        if self.config.autonomous_acceptance_max_entries != 1:
            blockers.append("VALIDATION_REQUIRES_ONE_ENTRY_MAX")
        if self.state.emergency_disabled:
            blockers.append("VALIDATION_EMERGENCY_DISABLED")
        if self.state.broker_account_verified_paper is False and self.state.broker_api_ready:
            blockers.append("VALIDATION_REQUIRES_VERIFIED_PAPER_ACCOUNT")
        return blockers

    async def _refresh_broker_readiness(self, *, connect: bool) -> bool:
        diagnostics: dict[str, Any] = {}
        try:
            adapter = broker_registry.get("ibkr")
            if connect:
                await adapter.connect()
            real_client = getattr(adapter, "real_client", None)
            manager = getattr(adapter, "session_manager", None)
            diagnostics = manager.diagnostics() if manager and hasattr(manager, "diagnostics") else {}
            api_ready = bool(real_client and getattr(real_client, "api_ready", False))
            if not api_ready:
                self._set_broker_unready("REAL_IBKR_API_NOT_READY", diagnostics=diagnostics)
                return False
            accounts = await adapter.accounts()
            paper_accounts = [row for row in accounts if row.paper_verified and row.allowed and str(getattr(row, "environment", "")).split(".")[-1] == "PAPER"]
            if not paper_accounts:
                self._set_broker_unready("VERIFIED_PAPER_ACCOUNT_REQUIRED", diagnostics=diagnostics)
                return False
            account_id = paper_accounts[0].account_id
            recon = await adapter.reconcile(account_id)
            self.state.broker_connected = True
            self.state.broker_api_ready = True
            self.state.broker_account = account_id
            self.state.broker_account_verified_paper = True
            self.state.broker_reconciliation = recon.status
            self.state.broker_readiness_error = None
            if recon.status in {"MATCHED", "MATCHED_EMPTY"}:
                self.state.broker_last_ready_at = utcnow()
                return True
            self._set_broker_unready(f"RECONCILIATION_{recon.status}", diagnostics=diagnostics)
            return False
        except Exception as exc:
            try:
                adapter = broker_registry.get("ibkr")
                manager = getattr(adapter, "session_manager", None)
                diagnostics = manager.diagnostics() if manager and hasattr(manager, "diagnostics") else diagnostics
            except Exception:
                pass
            self._set_broker_unready(_broker_error_code(exc), diagnostics=diagnostics)
            return False

    def _set_broker_unready(self, reason: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        self.state.broker_connected = bool((diagnostics or {}).get("socket_connected", False))
        self.state.broker_api_ready = bool((diagnostics or {}).get("api_ready", False))
        self.state.broker_account_verified_paper = False if reason == "VERIFIED_PAPER_ACCOUNT_REQUIRED" else self.state.broker_account_verified_paper
        self.state.broker_readiness_error = reason

    def _broker_status(self) -> dict[str, Any]:
        return {
            "broker_connected": self.state.broker_connected,
            "broker_api_ready": self.state.broker_api_ready,
            "broker_account": _mask_account(self.state.broker_account),
            "broker_account_verified_paper": self.state.broker_account_verified_paper,
            "broker_reconciliation": self.state.broker_reconciliation,
            "broker_readiness_error": self.state.broker_readiness_error,
            "broker_last_ready_at": self.state.broker_last_ready_at.isoformat() if self.state.broker_last_ready_at else None,
        }

    def _trading_date(self, at: datetime | None = None) -> str:
        try:
            tz = ZoneInfo(self.config.trading_day_timezone)
        except ZoneInfoNotFoundError:
            tz = timezone.utc
        return (at or utcnow()).astimezone(tz).date().isoformat()

    def _daily_entry_cap(self) -> int:
        return self.config.daily_acceptance_max_entries if self.config.multi_trade_validation_mode else self.config.autonomous_acceptance_max_entries

    def _daily_state(self) -> dict[str, Any]:
        self._ensure_daily_state()
        return get_state("ai_daily_acceptance_v1")

    def _save_daily_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = utcnow().isoformat()
        set_state("ai_daily_acceptance_v1", state)

    def _ensure_daily_state(self) -> dict[str, Any]:
        trading_date = self._trading_date()
        state = get_state("ai_daily_acceptance_v1")
        if state.get("trading_date") != trading_date:
            state = {
                "trading_date": trading_date,
                "timezone": self.config.trading_day_timezone,
                "entries_attempted": 0,
                "entries_submitted": 0,
                "entries_filled": 0,
                "trades_closed": 0,
                "trades_rejected": 0,
                "openai_calls": 0,
                "entries_by_symbol": {},
                "daily_gross_pnl": 0.0,
                "daily_net_pnl": 0.0,
                "daily_maximum_drawdown": 0.0,
                "consecutive_losses": 0,
                "daily_lock_reason": None,
                "created_at": utcnow().isoformat(),
            }
            if self.config.daily_acceptance_date == trading_date and self.config.daily_acceptance_entries_used:
                state["entries_submitted"] = self.config.daily_acceptance_entries_used
                state["entries_attempted"] = self.config.daily_acceptance_entries_used
            self._save_daily_state(state)
        return state

    def _entries_remaining(self) -> int:
        state = self._daily_state()
        return max(0, self._daily_entry_cap() - int(state.get("entries_submitted") or 0))

    def _symbol_entries_used(self, symbol: str) -> int:
        normalized = normalize_forex_symbol(symbol)
        state = self._daily_state()
        persisted = int((state.get("entries_by_symbol") or {}).get(normalized) or 0)
        memory = int(self.state.trades_by_symbol_today.get(normalized) or 0)
        return max(persisted, memory)

    def _daily_lock_active(self) -> bool:
        state = self._daily_state()
        return bool(state.get("daily_lock_reason")) or int(state.get("entries_submitted") or 0) >= self._daily_entry_cap()

    def _daily_loss_allowance_remaining(self) -> float:
        state = self._daily_state()
        realized = abs(min(0.0, float(state.get("daily_net_pnl") or 0.0)))
        return max(0.0, float(self.config.max_daily_loss_usd) - realized)

    def _cooldown_active(self) -> bool:
        return bool(self.state.cooldown_expiry and self.state.cooldown_expiry > utcnow())

    def _daily_validation_blockers(self) -> list[str]:
        if not self.config.multi_trade_validation_mode:
            return []
        state = self._daily_state()
        blockers: list[str] = []
        lock_reason = state.get("daily_lock_reason")
        if lock_reason:
            blockers.append(f"DAILY_LOCKED:{lock_reason}")
        if int(state.get("entries_submitted") or 0) >= self._daily_entry_cap():
            blockers.append("DAILY_ENTRY_CAP_REACHED")
        if float(state.get("daily_net_pnl") or 0.0) <= -abs(self.config.max_daily_loss_usd):
            blockers.append("DAILY_LOSS_LIMIT")
        if self.config.daily_profit_lock_usd and float(state.get("daily_net_pnl") or 0.0) >= self.config.daily_profit_lock_usd:
            blockers.append("DAILY_PROFIT_LOCK")
        if int(state.get("consecutive_losses") or 0) >= self.config.max_consecutive_losses:
            blockers.append("CONSECUTIVE_LOSS_LIMIT")
        if self._cooldown_active():
            blockers.append("POST_TRADE_COOLDOWN")
        return blockers

    def _record_daily_rejection(self, reasons: list[str]) -> None:
        if not self.config.multi_trade_validation_mode:
            return
        state = self._daily_state()
        state["trades_rejected"] = int(state.get("trades_rejected") or 0) + 1
        state["last_rejection_reasons"] = reasons
        self._save_daily_state(state)

    def _record_daily_submission(self, *, filled_quantity: Decimal) -> None:
        if not self.config.multi_trade_validation_mode:
            return
        state = self._daily_state()
        if filled_quantity > 0:
            state["entries_filled"] = int(state.get("entries_filled") or 0) + 1
        if int(state.get("entries_submitted") or 0) >= self._daily_entry_cap():
            state["daily_lock_reason"] = "DAILY_ENTRY_CAP_REACHED"
        self._save_daily_state(state)

    def _daily_status(self) -> dict[str, Any]:
        state = self._daily_state()
        return {
            "active_profile": self.state.active_threshold_profile,
            "trading_date": state.get("trading_date"),
            "timezone": self.config.trading_day_timezone,
            "daily_entry_cap": self._daily_entry_cap(),
            "entries_used": int(state.get("entries_submitted") or 0),
            "entries_remaining": self._entries_remaining(),
            "entries_by_symbol": dict(state.get("entries_by_symbol") or {}),
            "entries_attempted": int(state.get("entries_attempted") or 0),
            "entries_filled": int(state.get("entries_filled") or 0),
            "trades_closed": int(state.get("trades_closed") or 0),
            "trades_rejected": int(state.get("trades_rejected") or 0),
            "openai_calls": int(state.get("openai_calls") or 0),
            "cooldown_active": self._cooldown_active(),
            "cooldown_expiry": self.state.cooldown_expiry.isoformat() if self.state.cooldown_expiry else None,
            "consecutive_losses": int(state.get("consecutive_losses") or 0),
            "daily_gross_pnl": float(state.get("daily_gross_pnl") or 0.0),
            "daily_net_pnl": float(state.get("daily_net_pnl") or 0.0),
            "daily_drawdown": float(state.get("daily_maximum_drawdown") or 0.0),
            "daily_loss_allowance_remaining": self._daily_loss_allowance_remaining(),
            "daily_lock_active": self._daily_lock_active(),
            "daily_lock_reason": state.get("daily_lock_reason"),
        }

    def complete_acceptance_if_flat(self) -> None:
        state = get_state("ai_auto_paper_acceptance")
        if state.get("entry_used"):
            state["complete"] = True
            state["order_submission_enabled"] = False
            state["completed_at"] = utcnow().isoformat()
            state["validation_mode_enabled"] = False
            state["production_profile_restored"] = True
            set_state("ai_auto_paper_acceptance", state)
            self.state.acceptance_complete = True
            self.state.production_profile_restored = True
            self.state.active_threshold_profile = self.config.production_profile.name

    def _validation_mode_enabled(self) -> bool:
        if self.config.multi_trade_validation_mode:
            return False
        state = get_state("ai_auto_paper_acceptance")
        return bool(self.config.acceptance_validation_mode and not state.get("complete") and state.get("validation_mode_enabled", True) is not False)

    def _autonomous_submission_enabled(self) -> bool:
        if self.config.multi_trade_validation_mode:
            return self.config.trading_mode == "AUTO_PAPER" and self.config.order_submission_enabled and not self._daily_lock_active()
        state = get_state("ai_auto_paper_acceptance")
        if state.get("complete") or state.get("order_submission_enabled") is False:
            return False
        return self.config.trading_mode == "AUTO_PAPER" and self.config.order_submission_enabled

    def _execution_timeframes(self) -> tuple[str, ...]:
        supported = tuple(tf for tf in self.config.timeframe_allowlist if tf in {"5m", "15m"})
        return supported or ("15m",)

    def _sync_runtime_profile(self) -> None:
        self._ensure_daily_state()
        if self.config.multi_trade_validation_mode:
            self.state.active_threshold_profile = "PAPER_MULTI_TRADE_VALIDATION"
            self.state.production_profile_restored = False
            if self.config.acceptance_validation_mode:
                self.config = replace(self.config, acceptance_validation_mode=False)
            service_config = getattr(self.ai_service, "config", None)
            if service_config is not None and getattr(service_config, "acceptance_validation_mode", None):
                self.ai_service.config = replace(service_config, acceptance_validation_mode=False)
            return
        enabled = self._validation_mode_enabled()
        if self.config.acceptance_validation_mode != enabled:
            self.config = replace(self.config, acceptance_validation_mode=enabled)
        service_config = getattr(self.ai_service, "config", None)
        if service_config is not None and getattr(service_config, "acceptance_validation_mode", None) != enabled:
            self.ai_service.config = replace(service_config, acceptance_validation_mode=enabled)
        self.state.active_threshold_profile = self.config.validation_profile.name if enabled else self.config.production_profile.name
        self.state.production_profile_restored = not enabled

    def _acceptance_entry_used(self) -> bool:
        if self.config.multi_trade_validation_mode:
            return self._entries_remaining() <= 0 or self._daily_lock_active()
        state = get_state("ai_auto_paper_acceptance")
        return bool(self.config.autonomous_acceptance_entry_used or state.get("entry_used"))

    def _mark_acceptance_entry_used(self, symbol: str | None = None) -> None:
        if self.config.multi_trade_validation_mode:
            state = self._daily_state()
            state["entries_attempted"] = int(state.get("entries_attempted") or 0) + 1
            state["entries_submitted"] = int(state.get("entries_submitted") or 0) + 1
            state["openai_calls"] = int(state.get("openai_calls") or 0) + 1
            if symbol:
                by_symbol = dict(state.get("entries_by_symbol") or {})
                by_symbol[symbol] = int(by_symbol.get(symbol) or 0) + 1
                state["entries_by_symbol"] = by_symbol
            self._save_daily_state(state)
            return
        state = get_state("ai_auto_paper_acceptance")
        state.update({"entry_used": True, "entry_used_at": utcnow().isoformat(), "threshold_profile": self.state.active_threshold_profile})
        set_state("ai_auto_paper_acceptance", state)

    def _persist_intent(self, intent_id: str, order_ref: str, result: ShadowAnalysisResult, context: dict[str, Any], quantity: Decimal, max_loss: float, *, sizing: dict[str, Any] | None = None) -> None:
        state = get_state("ai_auto_paper_intents")
        items = [row for row in list(state.get("items") or []) if row.get("intent_id") != intent_id]
        items.insert(
            0,
            {
                "intent_id": intent_id,
                "order_ref": order_ref,
                "created_at": utcnow().isoformat(),
                "decision_id": result.decision_id,
                "symbol": result.symbol,
                "quantity": str(quantity),
                "maximum_loss": max_loss,
                "sizing": sizing or {},
                "threshold_profile": context.get("active_threshold_profile"),
                "consensus": context.get("strategy_consensus"),
                "conflict_classification": context.get("conflict_classification"),
            },
        )
        set_state("ai_auto_paper_intents", {"items": items[:100], "updated_at": utcnow().isoformat()})

    def _arm_validation_exit_if_needed(self) -> None:
        if self.config.acceptance_validation_mode and self.state.active_threshold_profile == "PAPER_ACCEPTANCE_VALIDATION":
            self.state.validation_exit_deadline = utcnow() + timedelta(minutes=self.config.validation_max_holding_minutes)
            state = get_state("ai_auto_paper_acceptance")
            state["validation_exit_deadline"] = self.state.validation_exit_deadline.isoformat()
            state["validation_timeout_exit_reason"] = "VALIDATION_TIMEOUT_EXIT"
            set_state("ai_auto_paper_acceptance", state)

    def _acquire_scheduler_lock(self, owner: str) -> bool:
        state = get_state("ai_scheduler_lock")
        now = utcnow()
        heartbeat = state.get("heartbeat_at")
        if state.get("owner") and heartbeat:
            try:
                heartbeat_dt = datetime.fromisoformat(str(heartbeat).replace("Z", "+00:00"))
                lease_seconds = max(self.config.analysis_interval_seconds + 60, 120)
                if now - heartbeat_dt < timedelta(seconds=lease_seconds):
                    self.state.scheduler_owner = str(state.get("owner"))
                    return state.get("owner") == owner
            except ValueError:
                pass
        set_state("ai_scheduler_lock", {"owner": owner, "heartbeat_at": now.isoformat(), "started_at": now.isoformat()})
        return True

    def _release_scheduler_lock(self) -> None:
        state = get_state("ai_scheduler_lock")
        if state.get("owner") == self.state.scheduler_owner:
            set_state("ai_scheduler_lock", {})

    def _heartbeat_scheduler_lock(self, owner: str) -> None:
        set_state("ai_scheduler_lock", {"owner": owner, "heartbeat_at": utcnow().isoformat(), "started_at": self.state.scheduler_started_at.isoformat() if self.state.scheduler_started_at else utcnow().isoformat()})

    async def _position_capacity_blockers(self) -> list[str]:
        try:
            adapter = broker_registry.get("ibkr")
            account_id = await self._paper_account_id(adapter)
            positions = await adapter.positions(account_id)
            orders = await adapter.open_orders(account_id)
            open_positions = _open_fx_positions(positions)
            self.state.current_position = open_positions[0] if open_positions else None
            self.state.current_position_count = len(open_positions)
            self.state.current_open_order_count = len(orders)
            blockers: list[str] = []
            if len(open_positions) >= self.config.max_open_positions:
                blockers.append("OPEN_POSITION_LIMIT")
            if len(orders) >= self.config.max_open_orders:
                blockers.append("OPEN_ORDER_LIMIT")
            if self._non_allowlisted_open_fx_symbols(open_positions):
                blockers.append("NON_ALLOWLISTED_FX_POSITION_OPEN")
            return blockers
        except Exception:
            return ["BROKER_POSITION_CAPACITY_UNKNOWN"]

    def _non_allowlisted_open_fx_symbols(self, open_positions: list[dict[str, Any]]) -> list[str]:
        return [
            str(row.get("symbol") or row.get("instrument_id") or "")
            for row in open_positions
            if _safe_normalize_fx_symbol(str(row.get("symbol") or row.get("instrument_id") or "")) not in self.config.symbol_allowlist
        ]

    async def _paper_account_id(self, adapter: Any) -> str:
        accounts = await adapter.accounts()
        paper = [
            row
            for row in accounts
            if row.paper_verified
            and row.allowed
            and str(getattr(row, "environment", "")).split(".")[-1] == "PAPER"
        ]
        if not paper:
            raise RuntimeError("VERIFIED_PAPER_ACCOUNT_REQUIRED")
        return paper[0].account_id

    def _assert_scheduler_owner(self, owner: str) -> None:
        if self.state.scheduler_owner and self.state.scheduler_owner != owner:
            raise RuntimeError("DUPLICATE_SCHEDULER_OWNER")
        self.state.scheduler_owner = owner

    def _persist_cycle(self, result: dict[str, Any], *, candidate: ScreenedCandidate | None = None, symbol: str | None = None) -> None:
        selected_symbol = symbol or (candidate.symbol if candidate else "NONE")
        candle_timestamp = None
        if candidate and candidate.context.get("data_timestamp"):
            try:
                candle_timestamp = datetime.fromisoformat(str(candidate.context["data_timestamp"]).replace("Z", "+00:00"))
            except ValueError:
                candle_timestamp = None
        save_cycle(
            {
                "id": str(result.get("cycle_id") or _cycle_id()),
                "symbol": selected_symbol,
                "timeframe": candidate.timeframe if candidate else "15m",
                "candle_timestamp": candle_timestamp,
                "status": str(result.get("status") or "UNKNOWN"),
                "deterministic_score": candidate.score if candidate else None,
                "context_hash": candidate.context_hash if candidate else None,
                "provider_calls": int(result.get("provider_calls") or 0),
                "place_order_calls": 1 if result.get("submitted") else 0,
                "reasons": list((result.get("blockers") or []) + (candidate.reasons if candidate else [])),
                "payload": _json_safe({**result, "threshold_profile": (candidate.context.get("active_threshold_profile") if candidate else self.state.active_threshold_profile)}),
                "updated_at": utcnow(),
            }
        )


def _cycle_id() -> str:
    now = utcnow()
    bucket = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    return f"ai2_{bucket.strftime('%Y%m%dT%H%M%SZ')}"


def _next_completed_run_time(timeframes: tuple[str, ...], now: datetime | None = None) -> datetime:
    interval = min(TIMEFRAME_SECONDS.get(tf, 900) for tf in (timeframes or ("15m",)))
    return _next_completed_run_time_for_interval(interval, now=now)


def _next_completed_15m_run_time(now: datetime | None = None) -> datetime:
    return _next_completed_run_time_for_interval(900, now=now)


def _next_completed_run_time_for_interval(interval: int, now: datetime | None = None) -> datetime:
    current = now or utcnow()
    step_minutes = max(1, interval // 60)
    minute_bucket = (current.minute // step_minutes) * step_minutes
    current_bucket = current.replace(minute=minute_bucket, second=0, microsecond=0)
    current_bucket_run = current_bucket + timedelta(seconds=15)
    if current < current_bucket_run:
        return current_bucket_run
    return current_bucket + timedelta(seconds=interval + 15)


def _candle_cycle_id(symbol: str, timeframe: str, data_timestamp: Any) -> str:
    safe_ts = str(data_timestamp or utcnow().isoformat()).replace(":", "").replace("+", "Z").replace("-", "")
    return f"ai2_{symbol}_{timeframe}_{safe_ts}"


def _candidate_row(row: ScreenedCandidate) -> dict[str, Any]:
    return {"symbol": row.symbol, "timeframe": row.timeframe, "score": row.score, "context_hash": row.context_hash, "reasons": row.reasons}


def _filled_quantity(receipt: dict[str, Any]) -> Decimal:
    total = Decimal("0")
    for row in receipt.get("executions") or []:
        execution = row.get("execution") or {}
        raw_qty = execution.get("quantity") or execution.get("shares") or "0"
        try:
            total += abs(Decimal(str(raw_qty)))
        except Exception:
            continue
    if total > 0:
        return total
    for row in receipt.get("statuses") or []:
        try:
            total = max(total, abs(Decimal(str(row.get("filled") or "0"))))
        except Exception:
            continue
    return total


def _broker_order_summary(receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_id": receipt.get("order_id"),
        "order_ref": receipt.get("order_ref"),
        "submission_state": receipt.get("submission_state"),
        "place_order_called": bool(receipt.get("place_order_called")),
        "filled_quantity": str(_filled_quantity(receipt)),
    }


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    return str(value)


def _fx_insufficient_cash_policy() -> str:
    policy = os.getenv("FX_INSUFFICIENT_CASH_POLICY", "REJECT").strip().upper()
    return policy if policy in {"REJECT", "REDUCE"} else "REJECT"


def _fx_min_order_quantity() -> Decimal:
    try:
        return max(Decimal("1"), Decimal(str(os.getenv("FX_MIN_ORDER_QUANTITY", "1"))).quantize(Decimal("1"), rounding=ROUND_FLOOR))
    except Exception:
        return Decimal("1")


def _reduced_funding_quantity(details: Any) -> Decimal:
    checks: list[Any] = []
    if isinstance(details, dict):
        if isinstance(details.get("checks"), list):
            checks.extend(details["checks"])
        if isinstance(details.get("failed_check"), dict):
            checks.append(details["failed_check"])
        if "maximum_affordable_quantity" in details:
            checks.append(details)
    quantities: list[Decimal] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        try:
            quantities.append(Decimal(str(check.get("maximum_affordable_quantity"))).quantize(Decimal("1"), rounding=ROUND_FLOOR))
        except Exception:
            continue
    return min(quantities) if quantities else Decimal("0")


def _first_fx_position(positions: list[Any]) -> dict[str, Any] | None:
    open_positions = _open_fx_positions(positions)
    return open_positions[0] if open_positions else None


def _open_fx_positions(positions: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in positions:
        quantity = getattr(row, "quantity", None)
        try:
            if Decimal(str(quantity)) == 0:
                continue
        except Exception:
            continue
        instrument_id = str(getattr(row, "instrument_id", "") or "").upper()
        symbol = str(getattr(row, "symbol", "") or "").upper()
        if instrument_id.startswith("FX:") or symbol in {"EURUSD", "GBPUSD", "USDJPY"}:
            out.append(
                {
                    "instrument_id": instrument_id or symbol,
                    "symbol": symbol or instrument_id.replace("FX:", ""),
                    "quantity": str(quantity),
                    "average_price": str(getattr(row, "average_price", "")),
                }
            )
    return out


def _safe_normalize_fx_symbol(value: str) -> str:
    raw = str(value or "").upper().replace("FX:", "")
    try:
        return normalize_forex_symbol(raw)
    except ValueError:
        return raw


def _broker_error_code(exc: Exception) -> str:
    if isinstance(exc, BrokerError):
        return exc.code
    return exc.__class__.__name__


def _mask_account(account_id: str | None) -> str | None:
    if not account_id:
        return None
    if len(account_id) <= 4:
        return "*" * len(account_id)
    return f"{account_id[:2]}***{account_id[-2:]}"
