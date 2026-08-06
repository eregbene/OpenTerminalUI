from __future__ import annotations

import hashlib
import json
import os
import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.adaptive_management import tp_protection
from backend.adaptive_management.orm import (
    AdaptiveActivationORM,
    AdaptiveBrokerActionResultORM,
    AdaptiveCircuitBreakerORM,
    AdaptiveExperimentORM,
    AdaptiveManagementActionORM,
    AdaptivePartialExitStageORM,
    AdaptivePositionAdoptionORM,
    AdaptivePositionStateORM,
    AdaptiveSessionORM,
    AdaptiveSessionReportORM,
    AdaptiveStopQualityAuditORM,
    AdaptiveTradeEventORM,
    ChampionChallengerResultORM,
    CounterfactualOutcomeORM,
    DriftSnapshotORM,
    MarketRegimeSnapshotORM,
    ShadowDecisionORM,
    TradeManagementPolicyORM,
    TradePathSnapshotORM,
    TradeThesisORM,
)
from backend.brokers.mt5 import account_registry
from backend.brokers.mt5.adapter import mt5_adapter
from backend.brokers.mt5.config import mt5_config
from backend.brokers.mt5.persistence import sanitize
from backend.decision_context.service import decision_context_service
from backend.economic_intelligence.service import economic_intelligence_service
from backend.intelligence.trading.config import ai_trading_config
from backend.portfolio_execution.service import execution_manager
from backend.shared.db import SessionLocal

REGIME_VERSION = "deterministic_regime_v1"
REWARD_VERSION = "adaptive_reward_v1"
ACTIVE_POLICY_ID = "conservative_demo_manager_v1"
ACTIVE_POLICY_VERSION = "v2"
logger = logging.getLogger(__name__)

# v2 action types added by the trade-sizing/profit-protection overhaul. Deliberately gated by
# their OWN independent shadow/enforce switch (v2_mode() below) rather than reusing the existing
# ADAPTIVE_TRADE_MANAGEMENT_MODE/demo_active gate -- so the brand-new dynamic-TP and
# reduced-risk-SL logic ships shadow-only by default even on an installation that already runs
# the existing manager in demo_active, and enabling it is a deliberate, separate opt-in.
V2_ACTION_TYPES = {"MOVE_SL_TO_REDUCED_RISK", "EXTEND_TP", "REDUCE_TP"}

# The set of action types that modify SL and/or TP on an existing broker position (as opposed to
# closing/reducing volume). Referenced at every point that needs to treat these uniformly:
# reconciliation confirmation matching, request building, and post-fill cooldown stamping.
SLTP_MODIFY_ACTION_TYPES = {"MOVE_SL_BREAKEVEN", "TRAIL_STOP", "TP_PROGRESS_STRUCTURE_STOP"} | V2_ACTION_TYPES


def v2_mode() -> str:
    """Independent shadow/enforce/disabled switch for the v2 action types (see
    V2_ACTION_TYPES). Read fresh on every call, matching AdaptiveManagementService.mode()'s
    convention, so monkeypatch.setenv works in tests. Default 'shadow': v2 actions are always
    computed and persisted, never executed, until an operator explicitly sets 'enforce'."""
    value = os.getenv("ADAPTIVE_MANAGEMENT_MODE", "shadow").strip().lower()
    return value if value in {"disabled", "shadow", "enforce"} else "shadow"


@dataclass(frozen=True)
class TradeCase:
    trade_id: str
    session_id: str | None
    symbol: str
    direction: str
    volume: float
    entry: float
    stop_loss: float
    take_profit: float
    entry_time: datetime
    exit_time: datetime | None
    actual_pnl: float
    commission: float = 0.0
    swap: float = 0.0
    spread_at_entry: float | None = None
    strategy_id: str = "UNKNOWN"
    strategy_version: str = "UNKNOWN"
    setup_id: str = "UNKNOWN"
    timeframe: str = "M5"


@dataclass(frozen=True)
class ManagementCandidate:
    action_type: str
    priority: int
    requested_volume: float | None = None
    requested_sl: float | None = None
    requested_tp: float | None = None
    requested_price: float | None = None
    reason: str = ""
    evidence: dict[str, Any] | None = None


DEFAULT_MINIMUMS = {
    "MIN_TRADES_PER_POLICY_GLOBAL": 20,
    "MIN_TRADES_PER_STRATEGY_POLICY": 10,
    "MIN_TRADES_PER_REGIME_POLICY": 8,
    "MIN_WINNING_TRADES": 5,
    "MIN_LOSING_TRADES": 5,
    "MIN_OUT_OF_SAMPLE_TRADES": 10,
    "MIN_DISTINCT_TRADING_DAYS": 5,
    "MIN_DISTINCT_REGIME_COUNT": 2,
}


class AdaptiveManagementService:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._cycle_lock = asyncio.Lock()
        self._last_reconciliation_at: datetime | None = None
        self._last_auto_replay_at: datetime | None = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        if self.mode() == "disabled":
            logger.warning("Adaptive trade manager disabled")
            return
        await self._ensure_env_activation()
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._monitor_loop(), name="adaptive-trade-management-monitor")
        logger.warning("Adaptive trade manager monitor started mode=%s", self.mode())

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        logger.warning("Adaptive trade manager monitor stopped")

    def mode(self) -> str:
        mode = os.getenv("ADAPTIVE_TRADE_MANAGEMENT_MODE", "shadow").strip().lower()
        return mode if mode in {"disabled", "shadow", "demo_active"} else "shadow"

    def status(self) -> dict[str, Any]:
        cfg = mt5_config()
        with SessionLocal() as db:
            activation = _active_activation(db)
            breaker = _breaker(db)
            return {
                "enabled": os.getenv("ADAPTIVE_TRADE_MANAGEMENT_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
                "mode": self.mode(),
                "broker_mutation_allowed": False,
                "demo_active_broker_mutation_allowed": bool(activation and self.mode() == "demo_active" and breaker.state == "closed"),
                "supported_modes": ["disabled", "shadow", "demo_active"],
                "live_trading_enabled": bool(cfg.live_trading_enabled),
                "order_submission_config_unchanged": True,
                "risk_config_observed_only": _effective_config(),
                "monitor_running": bool(self._task and not self._task.done()),
                "activation": _orm_dict(activation) if activation else None,
                "circuit_breaker": _orm_dict(breaker),
                "sessions": db.query(AdaptiveSessionORM).count(),
                "theses": db.query(TradeThesisORM).count(),
                "policies": db.query(TradeManagementPolicyORM).count(),
                "shadow_decisions": db.query(ShadowDecisionORM).count(),
                "counterfactual_outcomes": db.query(CounterfactualOutcomeORM).count(),
            }

    async def activate_demo(
        self,
        *,
        approved_by: str = "local_env",
        eligible_symbols: list[str] | None = None,
        eligible_strategies: list[str] | None = None,
        maximum_actions_per_hour: int = 6,
        effective_from: datetime | None = None,
    ) -> dict[str, Any]:
        if self.mode() != "demo_active":
            return {"status": "REJECTED", "reason": "ADAPTIVE_TRADE_MANAGEMENT_MODE_MUST_BE_demo_active", "mode": self.mode()}
        cfg = mt5_config()
        if cfg.live_trading_enabled:
            return {"status": "REJECTED", "reason": "LIVE_TRADING_BLOCKED"}
        account = await mt5_adapter.mt5_account()
        terminal = await mt5_adapter.terminal_status()
        if terminal.account_mode != "DEMO" or cfg.account_mode != "DEMO":
            return {"status": "REJECTED", "reason": "MT5_DEMO_ACCOUNT_REQUIRED"}
        fingerprint = account_registry.fingerprint_account(account)
        account_registry.record_sighting(fingerprint, account_mode=cfg.account_mode, profile_label=os.getenv("MT5_ACCOUNT_PROFILE_LABEL"))
        now = utcnow()
        activation_id = "ADAPTIVE_ACT_" + _hash({"account": account.login, "policy": ACTIVE_POLICY_ID, "time": (effective_from or now).isoformat()})[:24]
        with SessionLocal() as db:
            existing = _active_activation(db)
            if existing:
                return {"status": "ALREADY_ACTIVE", "activation": _orm_dict(existing)}
            activation = AdaptiveActivationORM(activation_id=activation_id)
            activation.policy_id = ACTIVE_POLICY_ID
            activation.policy_version = ACTIVE_POLICY_VERSION
            activation.mode = "demo_active"
            activation.demo_account = str(account.login)
            activation.account_fingerprint = fingerprint.fingerprint_hash
            activation.effective_from = effective_from or now
            activation.approved_by = approved_by
            activation.approved_at = now
            activation.eligible_symbols = [row.upper() for row in (eligible_symbols or [])]
            activation.eligible_strategies = eligible_strategies or ["BENSIM_AUTO"]
            activation.maximum_actions_per_hour = max(1, min(20, int(maximum_actions_per_hour)))
            activation.rollback_policy = {"on_breaker_open": "shadow_only", "preserve_broker_sl_tp": True}
            activation.emergency_state = "normal"
            activation.configuration_snapshot = _effective_config(account.model_dump(mode="json"))
            activation.active = True
            db.merge(activation)
            breaker = _breaker(db)
            breaker.state = "closed"
            breaker.reason = None
            breaker.failed_actions = 0
            breaker.rejected_actions = 0
            breaker.actions_this_hour = 0
            db.merge(breaker)
            db.commit()
            return {"status": "ACTIVE", "activation": _orm_dict(activation), "broker_mutation_allowed": True}

    def deactivate(self, reason: str = "manual_deactivate") -> dict[str, Any]:
        with SessionLocal() as db:
            activation = _active_activation(db)
            if not activation:
                return {"status": "NO_ACTIVE_ACTIVATION"}
            activation.active = False
            activation.emergency_state = "deactivated"
            activation.rollback_policy = {**(activation.rollback_policy or {}), "deactivation_reason": reason}
            activation.updated_at = utcnow()
            db.merge(activation)
            db.commit()
            return {"status": "DEACTIVATED", "activation_id": activation.activation_id}

    def reset_circuit_breaker(self, reason: str = "manual_reset") -> dict[str, Any]:
        with SessionLocal() as db:
            breaker = _breaker(db)
            breaker.state = "closed"
            breaker.reason = reason
            breaker.failed_actions = 0
            breaker.rejected_actions = 0
            breaker.actions_this_hour = 0
            breaker.reset_at = utcnow()
            breaker.updated_at = utcnow()
            db.merge(breaker)
            db.commit()
            return {"status": "RESET", "breaker": _orm_dict(breaker)}

    def adoption(self, position_id: str, approved_by: str = "local_api", reason: str = "") -> dict[str, Any]:
        with SessionLocal() as db:
            activation = _active_activation(db)
            if not activation:
                return {"status": "REJECTED", "reason": "NO_ACTIVE_ACTIVATION"}
            adoption_id = "ADOPT_" + _hash({"position": position_id, "activation": activation.activation_id})[:32]
            row = db.get(AdaptivePositionAdoptionORM, adoption_id) or AdaptivePositionAdoptionORM(adoption_id=adoption_id)
            row.position_id = position_id
            row.activation_id = activation.activation_id
            row.approved_by = approved_by
            row.approved_at = utcnow()
            row.reason = reason
            row.active = True
            db.merge(row)
            state = db.get(AdaptivePositionStateORM, position_id)
            if state:
                state.adopted = True
                db.merge(state)
            db.commit()
            return {"status": "ADOPTED", "adoption": _orm_dict(row)}

    async def evaluate_now(self) -> dict[str, Any]:
        return await self._monitor_cycle()

    async def _ensure_env_activation(self) -> None:
        if self.mode() != "demo_active":
            return
        with SessionLocal() as db:
            if _active_activation(db):
                return
        symbols = _env_csv("ADAPTIVE_MANAGEMENT_ELIGIBLE_SYMBOLS") or _env_csv("MT5_TRADING_SYMBOLS")
        try:
            result = await self.activate_demo(
                approved_by=os.getenv("ADAPTIVE_MANAGEMENT_APPROVED_BY", "env_auto_activation"),
                eligible_symbols=symbols,
                eligible_strategies=_env_csv("ADAPTIVE_MANAGEMENT_ELIGIBLE_STRATEGIES") or ["BENSIM_AUTO"],
                maximum_actions_per_hour=_env_int("ADAPTIVE_MAX_ACTIONS_PER_HOUR", 6, minimum=1, maximum=20),
            )
            logger.warning("Adaptive trade manager env activation result=%s", result.get("status"))
        except Exception as exc:
            logger.warning("Adaptive trade manager waiting for MT5 before env activation: %s", exc.__class__.__name__)

    async def open_positions(self) -> dict[str, Any]:
        positions = await mt5_adapter.mt5_positions()
        with SessionLocal() as db:
            states = {row.position_id: _orm_dict(row) for row in db.query(AdaptivePositionStateORM).all()}
        return {"items": [position.model_dump(mode="json") | {"adaptive_state": states.get(_position_id(position.model_dump(mode="json")))} for position in positions]}

    async def position_detail(self, ticket: str) -> dict[str, Any]:
        positions = await mt5_adapter.mt5_positions()
        live = next((p for p in positions if _position_id(p.model_dump(mode="json")) == ticket), None)
        with SessionLocal() as db:
            state = db.get(AdaptivePositionStateORM, ticket)
            audit = db.query(AdaptiveStopQualityAuditORM).filter(AdaptiveStopQualityAuditORM.position_id == ticket).first()
            stages = [_orm_dict(row) for row in db.query(AdaptivePartialExitStageORM).filter(AdaptivePartialExitStageORM.position_id == ticket).order_by(AdaptivePartialExitStageORM.created_at.asc()).all()]
        if live is None and state is None:
            return {"found": False, "ticket": ticket}
        return {
            "found": True,
            "ticket": ticket,
            "live_position": live.model_dump(mode="json") if live else None,
            "adaptive_state": _orm_dict(state) if state else None,
            "stop_quality_audit": _orm_dict(audit) if audit else None,
            "partial_exit_stages": stages,
        }

    def position_history(self, ticket: str) -> dict[str, Any]:
        with SessionLocal() as db:
            actions = db.query(AdaptiveManagementActionORM).filter(AdaptiveManagementActionORM.position_id == ticket).order_by(AdaptiveManagementActionORM.created_at.asc()).all()
            action_ids = [row.action_id for row in actions]
            broker_results = (
                db.query(AdaptiveBrokerActionResultORM).filter(AdaptiveBrokerActionResultORM.action_id.in_(action_ids)).order_by(AdaptiveBrokerActionResultORM.created_at.asc()).all()
                if action_ids
                else []
            )
            events = db.query(AdaptiveTradeEventORM).filter(AdaptiveTradeEventORM.position_id == ticket).order_by(AdaptiveTradeEventORM.utc_time.asc()).all()
        return {
            "ticket": ticket,
            "management_actions": [_orm_dict(row) for row in actions],
            "broker_action_results": [_orm_dict(row) for row in broker_results],
            "trade_events": [_orm_dict(row) for row in events],
        }

    def performance(self) -> dict[str, Any]:
        return self.audit_report(session_id=None)

    def shadow_summary(self) -> dict[str, Any]:
        with SessionLocal() as db:
            activation = _active_activation(db)
            # AdaptiveManagementActionORM predates account_fingerprint and has no account column
            # to filter on directly. A position can never span an account switch, so actions
            # created before the CURRENT activation started cannot belong to the currently
            # connected account -- scoping by activation.created_at keeps a prior account's
            # shadow history out of this account's summary without needing a data migration.
            query = db.query(AdaptiveManagementActionORM).filter(AdaptiveManagementActionORM.mode == "shadow")
            if activation:
                query = query.filter(AdaptiveManagementActionORM.created_at >= activation.created_at)
            shadow_actions = query.order_by(AdaptiveManagementActionORM.created_at.desc()).limit(500).all()
        by_action_type: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for row in shadow_actions:
            by_action_type[row.action_type] = by_action_type.get(row.action_type, 0) + 1
            by_status[row.status] = by_status.get(row.status, 0) + 1
        return {
            "mode": self.mode(),
            "v2_mode": v2_mode(),
            "account_fingerprint": activation.account_fingerprint if activation else None,
            "scoped_from": activation.created_at.isoformat() if activation else None,
            "sample_size": len(shadow_actions),
            "by_action_type": by_action_type,
            "by_status": by_status,
            # Invariant check: shadow-mode candidates must never reach the broker. Any nonzero
            # value here indicates a gating bug, not expected behavior.
            "shadow_broker_mutation_leak_count": sum(1 for row in shadow_actions if row.broker_mutation_attempted),
            "recent": [_orm_dict(row) for row in shadow_actions[:50]],
        }

    async def run_shadow_cycle(self) -> dict[str, Any]:
        """Runs one evaluation cycle and reports it tagged with the current mode/v2_mode so
        callers can see whether anything could have mutated the broker. Does not itself change
        ADAPTIVE_TRADE_MANAGEMENT_MODE/ADAPTIVE_MANAGEMENT_MODE -- those remain env-controlled,
        so calling this route can never silently enable enforce mode."""
        result = await self._monitor_cycle()
        return {**result, "mode": self.mode(), "v2_mode": v2_mode()}

    def activation(self) -> dict[str, Any]:
        with SessionLocal() as db:
            activation = _active_activation(db)
            return {"activation": _orm_dict(activation) if activation else None, "mode": self.mode()}

    def actions(self) -> list[dict[str, Any]]:
        with SessionLocal() as db:
            return [_orm_dict(row) for row in db.query(AdaptiveManagementActionORM).order_by(AdaptiveManagementActionORM.created_at.desc()).limit(200).all()]

    def reconciliation(self) -> dict[str, Any]:
        with SessionLocal() as db:
            return {"items": [_orm_dict(row) for row in db.query(AdaptiveBrokerActionResultORM).order_by(AdaptiveBrokerActionResultORM.created_at.desc()).limit(200).all()]}

    async def import_mt5_session(self, days: int = 7, session_id: str | None = None) -> dict[str, Any]:
        history = await mt5_adapter.history(days=max(1, min(90, int(days))))
        account = await mt5_adapter.mt5_account()
        positions = await mt5_adapter.mt5_positions()
        orders = await mt5_adapter.mt5_orders()
        payload = {
            "session_id": session_id,
            "account_id": str(account.login),
            "server": account.server,
            "account_mode": "DEMO",
            "orders": [row.model_dump(mode="json") for row in history["orders"]],
            "deals": [row.model_dump(mode="json") for row in history["deals"]],
            "open_positions": [row.model_dump(mode="json") for row in positions],
            "open_orders": [row.model_dump(mode="json") for row in orders],
            "effective_config": _effective_config(account.model_dump(mode="json")),
        }
        return self.import_session(payload)

    def import_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = str(payload.get("session_id") or f"MT5_SESSION_{utcnow().strftime('%Y%m%d%H%M%S')}")
        deals = [_normalize_history_row(row, session_id, "DEAL") for row in payload.get("deals") or []]
        orders = [_normalize_history_row(row, session_id, "ORDER") for row in payload.get("orders") or []]
        positions = [_normalize_position_row(row, session_id) for row in payload.get("open_positions") or []]
        events = deals + orders + positions
        event_times: list[datetime] = [row["utc_time"] for row in events if isinstance(row.get("utc_time"), datetime)]
        started_at = min(event_times) if event_times else None
        ended_at = max(event_times) if event_times else None
        realized = sum(float(row.get("realized_pnl") or 0) + float(row.get("commission") or 0) + float(row.get("swap") or 0) + float(row.get("fee") or 0) for row in deals if _is_trade_symbol(row.get("symbol")))
        with SessionLocal() as db:
            session = db.get(AdaptiveSessionORM, session_id) or AdaptiveSessionORM(session_id=session_id)
            session.source = "MT5"
            session.account_id = str(payload.get("account_id") or "")
            session.account_mode = str(payload.get("account_mode") or "DEMO").upper()
            session.server = payload.get("server")
            session.started_at = started_at
            session.ended_at = ended_at
            session.realized_pnl = realized
            session.imported_trades = len({row.get("trade_id") for row in deals if row.get("trade_id") and _is_trade_symbol(row.get("symbol"))})
            session.imported_orders = len(orders)
            session.imported_deals = len(deals)
            session.effective_config = sanitize(payload.get("effective_config") or _effective_config())
            session.updated_at = utcnow()
            db.merge(session)
            for event in events:
                db.merge(_event_orm(event))
            db.commit()
        thesis = self.group_theses(session_id)
        return {"session_id": session_id, "imported_events": len(events), "imported_deals": len(deals), "imported_orders": len(orders), "open_positions": len(positions), "realized_pnl": realized, "thesis_groups": thesis["theses"]}

    def group_theses(self, session_id: str) -> dict[str, Any]:
        with SessionLocal() as db:
            events = db.query(AdaptiveTradeEventORM).filter(AdaptiveTradeEventORM.session_id == session_id, AdaptiveTradeEventORM.event_type.in_(["DEAL", "POSITION"])).order_by(AdaptiveTradeEventORM.utc_time.asc()).all()
            db.query(TradeThesisORM).filter(TradeThesisORM.session_id == session_id).delete(synchronize_session=False)
            groups: dict[tuple[str, str, str, str, str], list[AdaptiveTradeEventORM]] = {}
            for event in events:
                if not _is_trade_symbol(event.symbol):
                    continue
                key = (event.symbol, event.side or "UNKNOWN", event.strategy_id, event.setup_id, event.timeframe)
                groups.setdefault(key, []).append(event)
            count = 0
            for key, rows in groups.items():
                symbol, direction, strategy_id, setup_id, timeframe = key
                thesis_id = "THESIS_" + _hash({"session": session_id, "key": key})[:32]
                trade_ids = sorted({row.trade_id or row.position_id or row.deal_id or row.event_id for row in rows})
                pnls = [float(row.realized_pnl or 0) + float(row.commission or 0) + float(row.swap or 0) + float(row.fee or 0) for row in rows]
                volumes = [float(row.volume or 0) for row in rows]
                first = pnls[0] if pnls else None
                additional = sum(pnls[1:]) if len(pnls) > 1 else 0.0
                relationships = _relationship_summary(rows)
                thesis = db.get(TradeThesisORM, thesis_id) or TradeThesisORM(thesis_id=thesis_id)
                thesis.session_id = session_id
                thesis.symbol = symbol
                thesis.direction = direction
                thesis.strategy_id = strategy_id
                thesis.setup_id = setup_id
                thesis.timeframe = timeframe
                thesis.signal_timestamp = rows[0].utc_time if rows else None
                thesis.market_regime = relationships.get("market_regime", "insufficient_data")
                thesis.relationship_summary = relationships
                thesis.trade_ids = trade_ids
                thesis.trades_per_thesis = len(trade_ids)
                thesis.total_volume = sum(volumes)
                thesis.total_pnl = sum(pnls)
                thesis.peak_exposure = sum(volumes)
                thesis.first_entry_result = first
                thesis.additional_entry_contribution = additional
                thesis.extra_entries_helped = additional > 0 if len(pnls) > 1 else None
                thesis.correlation_score = _correlation_proxy(rows)
                thesis.raw_payload = sanitize({"events": [row.event_id for row in rows]})
                thesis.updated_at = utcnow()
                db.merge(thesis)
                count += 1
            db.commit()
        return {"session_id": session_id, "theses": count}

    def reconstruct_trade_path(self, trade: dict[str, Any], candles: list[dict[str, Any]], context: dict[str, Any] | None = None) -> dict[str, Any]:
        case = _trade_case(trade)
        path = reconstruct_path(case, candles, context=context or {})
        with SessionLocal() as db:
            row = db.get(TradePathSnapshotORM, path["path_id"]) or TradePathSnapshotORM(path_id=path["path_id"])
            _assign(row, path)
            db.merge(row)
            for regime in path.get("regime_snapshots") or []:
                db.merge(_regime_orm(regime))
            db.commit()
        return path

    def ensure_default_policies(self) -> dict[str, Any]:
        with SessionLocal() as db:
            policies = default_policies()
            for policy in policies:
                row = db.get(TradeManagementPolicyORM, policy["policy_id"]) or TradeManagementPolicyORM(policy_id=policy["policy_id"])
                _assign(row, policy)
                db.merge(row)
            db.commit()
        return {"policies": len(policies), "mode": "shadow", "broker_mutation_allowed": False}

    def replay(self, trade: dict[str, Any], candles: list[dict[str, Any]], policy_ids: list[str] | None = None) -> dict[str, Any]:
        self.ensure_default_policies()
        case = _trade_case(trade)
        path = reconstruct_path(case, candles)
        policies = [policy for policy in default_policies() if not policy_ids or policy["policy_id"] in set(policy_ids)]
        outcomes = [simulate_policy(case, path, policy) for policy in policies]
        with SessionLocal() as db:
            path_row = db.get(TradePathSnapshotORM, path["path_id"]) or TradePathSnapshotORM(path_id=path["path_id"])
            _assign(path_row, path)
            db.merge(path_row)
            for outcome in outcomes:
                db.merge(_shadow_decision_orm(outcome["first_shadow_decision"]))
                db.merge(_outcome_orm(outcome))
            db.commit()
        return {"trade_id": case.trade_id, "baseline_policy_id": "static_baseline_v1", "path": path, "outcomes": outcomes, "broker_mutation_calls": 0}

    def evaluate_policies(self, session_id: str | None = None) -> dict[str, Any]:
        with SessionLocal() as db:
            query = db.query(CounterfactualOutcomeORM)
            if session_id:
                trade_ids = [row.trade_id for row in db.query(AdaptiveTradeEventORM.trade_id).filter(AdaptiveTradeEventORM.session_id == session_id, AdaptiveTradeEventORM.trade_id.isnot(None)).distinct()]
                query = query.filter(CounterfactualOutcomeORM.trade_id.in_(trade_ids))
            rows = query.all()
        grouped: dict[str, list[CounterfactualOutcomeORM]] = {}
        for row in rows:
            grouped.setdefault(row.policy_id, []).append(row)
        results = [_policy_score(policy_id, policy_rows) for policy_id, policy_rows in grouped.items()]
        results.sort(key=lambda item: item["score"], reverse=True)
        return {"items": results, "minimums": DEFAULT_MINIMUMS, "promotion_allowed": False, "manual_approval_required": True}

    def run_walk_forward(self, session_id: str | None = None, train_fraction: float = 0.6) -> dict[str, Any]:
        with SessionLocal() as db:
            rows = db.query(CounterfactualOutcomeORM).order_by(CounterfactualOutcomeORM.created_at.asc()).all()
        if session_id:
            with SessionLocal() as db:
                trade_ids = {row.trade_id for row in db.query(AdaptiveTradeEventORM.trade_id).filter(AdaptiveTradeEventORM.session_id == session_id, AdaptiveTradeEventORM.trade_id.isnot(None)).distinct()}
            rows = [row for row in rows if row.trade_id in trade_ids]
        split = max(1, int(len(rows) * train_fraction)) if rows else 0
        train = rows[:split]
        test = rows[split:]
        train_scores = {policy: _policy_score(policy, policy_rows) for policy, policy_rows in _group_outcomes(train).items()}
        selected = max(train_scores.values(), key=lambda item: item["score"], default=None)
        selected_policy = selected["policy_id"] if selected else None
        test_score = _policy_score(selected_policy, [row for row in test if row.policy_id == selected_policy]) if selected_policy else _empty_policy_score(None)
        experiment_id = "ADAPTIVE_WF_" + _hash({"session": session_id, "rows": [row.outcome_id for row in rows]})[:24]
        degradation = (selected or {"score": 0})["score"] - test_score["score"] if selected else None
        result = {
            "experiment_id": experiment_id,
            "selected_policy_id": selected_policy,
            "train_count": len(train),
            "out_of_sample_count": len(test),
            "train_result": selected or {},
            "out_of_sample_result": test_score,
            "degradation": degradation,
            "chronological": True,
            "parameter_stability": _parameter_stability(train_scores),
            "minimums_passed": _minimums_pass(test),
            "promotion_allowed": False,
            "manual_approval_required": True,
        }
        with SessionLocal() as db:
            exp = db.get(AdaptiveExperimentORM, experiment_id) or AdaptiveExperimentORM(experiment_id=experiment_id)
            exp.experiment_type = "walk_forward"
            exp.status = "insufficient_data" if not result["minimums_passed"] else "shadow_candidate"
            exp.train_period = {"rows": len(train)}
            exp.validation_period = {}
            exp.out_of_sample_period = {"rows": len(test)}
            exp.selected_policy_id = selected_policy
            exp.parameter_stability = result["parameter_stability"]
            exp.train_result = result["train_result"]
            exp.out_of_sample_result = result["out_of_sample_result"]
            exp.degradation = degradation
            exp.regime_coverage = {}
            exp.reward_definition = reward_definition()
            exp.immutable = True
            exp.promotion_requires_manual_approval = True
            exp.raw_payload = sanitize(result)
            db.merge(exp)
            self._champion_challenger(db, experiment_id, rows)
            db.commit()
        return result

    def detect_drift(self, symbol: str | None = None) -> dict[str, Any]:
        with SessionLocal() as db:
            query = db.query(CounterfactualOutcomeORM)
            if symbol:
                trade_ids = [row.trade_id for row in db.query(AdaptiveTradeEventORM.trade_id).filter(AdaptiveTradeEventORM.symbol == symbol.upper(), AdaptiveTradeEventORM.trade_id.isnot(None)).distinct()]
                query = query.filter(CounterfactualOutcomeORM.trade_id.in_(trade_ids))
            rows = query.order_by(CounterfactualOutcomeORM.created_at.asc()).all()
        values = [row.hypothetical_pnl for row in rows if row.policy_id == "static_baseline_v1"]
        state = _drift_state(values)
        payload = {"count": len(values), "first_half_avg": _avg(values[: len(values) // 2]), "second_half_avg": _avg(values[len(values) // 2 :]), "state": state}
        drift_id = "DRIFT_" + _hash({"symbol": symbol, "payload": payload})[:32]
        with SessionLocal() as db:
            row = DriftSnapshotORM(drift_id=drift_id)
            row.scope = "SYMBOL" if symbol else "GLOBAL"
            row.symbol = symbol.upper() if symbol else None
            row.state = state
            row.metrics = payload
            row.recommendation = "Suspend promotion and continue shadow validation." if state in {"warning", "degraded", "significant_drift"} else "No adaptive execution change."
            db.merge(row)
            db.commit()
        return payload | {"drift_id": drift_id, "broker_mutation_calls": 0}

    def report(self, session_id: str) -> dict[str, Any]:
        evaluation = self.evaluate_policies(session_id=session_id)
        with SessionLocal() as db:
            session = db.get(AdaptiveSessionORM, session_id)
            theses = db.query(TradeThesisORM).filter(TradeThesisORM.session_id == session_id).all()
            events = db.query(AdaptiveTradeEventORM).filter(AdaptiveTradeEventORM.session_id == session_id).all()
        baseline = next((row for row in evaluation["items"] if row["policy_id"] == "static_baseline_v1"), _empty_policy_score("static_baseline_v1"))
        challenger = next((row for row in evaluation["items"] if row["policy_id"] != "static_baseline_v1"), _empty_policy_score(None))
        report = {
            "session_id": session_id,
            "actual_result": {"realized_pnl": session.realized_pnl if session else 0, "events": len(events)},
            "baseline_result": baseline,
            "challenger_result": challenger,
            "theses": [_orm_dict(row) for row in theses],
            "sample_size": max(row.get("sample_size", 0) for row in evaluation["items"]) if evaluation["items"] else 0,
            "uncertainty": {"minimums": DEFAULT_MINIMUMS, "insufficient_data": True},
            "recommendation": "COLLECT_MORE_DATA",
            "insufficient_data": True,
            "broker_mutation_calls": 0,
        }
        with SessionLocal() as db:
            row = db.get(AdaptiveSessionReportORM, f"REPORT_{session_id}") or AdaptiveSessionReportORM(report_id=f"REPORT_{session_id}")
            row.session_id = session_id
            row.actual_result = report["actual_result"]
            row.baseline_result = report["baseline_result"]
            row.challenger_result = report["challenger_result"]
            row.sample_size = report["sample_size"]
            row.uncertainty = report["uncertainty"]
            row.recommendation = report["recommendation"]
            row.insufficient_data = True
            row.raw_payload = sanitize(report)
            db.merge(row)
            db.commit()
        return report

    def audit_report(self, session_id: str | None = None) -> dict[str, Any]:
        with SessionLocal() as db:
            states = db.query(AdaptivePositionStateORM).all()
            audits = {row.position_id: row for row in db.query(AdaptiveStopQualityAuditORM).all()}
            events_query = db.query(AdaptiveTradeEventORM).filter(AdaptiveTradeEventORM.event_type == "DEAL")
            if session_id:
                events_query = events_query.filter(AdaptiveTradeEventORM.session_id == session_id)
            deals_by_position: dict[str, list[AdaptiveTradeEventORM]] = {}
            for row in events_query.all():
                key = row.position_id or row.trade_id
                if key:
                    deals_by_position.setdefault(key, []).append(row)
            actions_by_position: dict[str, list[AdaptiveManagementActionORM]] = {}
            for row in db.query(AdaptiveManagementActionORM).filter(AdaptiveManagementActionORM.status == "submitted").all():
                actions_by_position.setdefault(row.position_id, []).append(row)

        records: list[dict[str, Any]] = []
        for state in states:
            deals = deals_by_position.get(state.position_id, [])
            realized_pnl = sum(float(d.realized_pnl or 0) + float(d.commission or 0) + float(d.swap or 0) + float(d.fee or 0) for d in deals)
            exit_reason = next((d.broker_exit_reason for d in deals if d.broker_exit_reason), None)
            comment = (state.raw_payload or {}).get("comment")
            strategy_id = state.strategy_id or next((d.strategy_id for d in deals if d.strategy_id and d.strategy_id != "UNKNOWN"), _lineage(comment, "strategy"))
            timeframe = state.timeframe or next((d.timeframe for d in deals if d.timeframe and d.timeframe != "UNKNOWN"), _lineage(comment, "timeframe"))
            stop_audit = audits.get(state.position_id)
            original_target_distance = abs(float(state.original_tp or 0) - float(state.entry_price or 0)) if state.original_tp else None
            original_stop_distance = abs(float(state.entry_price or 0) - float(state.original_sl or 0)) if state.original_sl else None
            initial_rr = (original_target_distance / original_stop_distance) if original_target_distance and original_stop_distance else None
            max_tp_progress = state.max_tp_progress
            max_achieved_r = float(state.max_achieved_r or 0)
            giveback_r = float(state.current_giveback_r or 0)
            giveback_ratio = (giveback_r / max_achieved_r) if max_achieved_r > 0 else None
            exit_deal = max(deals, key=lambda d: d.utc_time or datetime.min.replace(tzinfo=timezone.utc), default=None)
            exit_price = float(exit_deal.price) if exit_deal is not None and exit_deal.price is not None else None
            direction_sign = 1.0 if (state.direction or "").upper() == "LONG" else -1.0
            exit_r = ((exit_price - float(state.entry_price or 0)) * direction_sign / original_stop_distance) if exit_price is not None and original_stop_distance else None
            actions = actions_by_position.get(state.position_id, [])
            action_types_taken = {row.action_type for row in actions}
            records.append(
                {
                    "position_id": state.position_id,
                    "symbol": state.symbol,
                    "strategy_id": strategy_id,
                    "timeframe": timeframe,
                    "session": _session_label(state.opened_at),
                    "volatility_state": _volatility_state_label(stop_audit),
                    "policy_version": ACTIVE_POLICY_VERSION,
                    "original_entry": state.entry_price,
                    "original_sl": state.original_sl,
                    "original_tp": state.original_tp,
                    "initial_stop_distance": original_stop_distance,
                    "initial_target_distance": original_target_distance,
                    "initial_risk_reward": initial_rr,
                    "sl_atr_multiple": stop_audit.sl_atr_multiple if stop_audit else None,
                    "mfe_r": max_achieved_r,
                    "mae_r": state.min_achieved_r,
                    "exit_r": exit_r,
                    "mfe_capture_ratio": (exit_r / max_achieved_r) if exit_r is not None and max_achieved_r > 0 else None,
                    "max_tp_progress": max_tp_progress,
                    "max_tp_progress_at": state.max_tp_progress_at.isoformat() if state.max_tp_progress_at else None,
                    "profit_retracement_after_mfe_r": giveback_r,
                    "giveback_ratio": giveback_ratio,
                    "giveback_over_50pct": bool(giveback_ratio is not None and giveback_ratio > 0.5),
                    "exit_reason": exit_reason,
                    "realized_pnl": realized_pnl,
                    "ever_profitable": bool(max_achieved_r > 0),
                    "reached_0_5r": bool(max_achieved_r >= 0.5),
                    "reached_1r": bool(max_achieved_r >= 1.0),
                    "reached_0_5r_then_lost": bool(max_achieved_r >= 0.5 and realized_pnl <= 0),
                    "reached_1r_then_lost": bool(max_achieved_r >= 1.0 and realized_pnl <= 0),
                    "original_tp_later_reached": bool(max_tp_progress and max_tp_progress >= 0.999),
                    "original_sl_later_reached": bool(state.min_achieved_r and state.min_achieved_r <= -0.999),
                    "management_cut_future_winner": bool(exit_reason not in (None, "TAKE_PROFIT") and max_tp_progress and max_tp_progress >= 0.60 and realized_pnl <= 0),
                    "management_failed_to_protect_large_profit": bool(max_tp_progress and max_tp_progress >= 0.80 and realized_pnl <= 0),
                    "stop_inside_normal_volatility": bool(stop_audit and "stop_too_tight" in (stop_audit.flags or [])),
                    "stop_quality_classification": stop_audit.classification if stop_audit else state.stop_quality_classification,
                    "stop_quality_classification_v2": stop_audit.classification_v2 if stop_audit else state.stop_quality_v2,
                    "winner_classification": state.winner_classification,
                    "would_breakeven_precede_later_tp": bool(state.max_achieved_r and state.max_achieved_r >= 1.0 and max_tp_progress and max_tp_progress < 1.0),
                    "breakeven_action_taken": bool(action_types_taken & {"MOVE_SL_BREAKEVEN", "MOVE_SL_TO_TRUE_BREAK_EVEN"}),
                    "partial_profit_action_taken": bool(action_types_taken & {"PARTIAL_PROFIT", "TP_PROGRESS_PARTIAL_PROTECT", "TP_PROGRESS_PROFIT_LOCK", "LOCK_PARTIAL_PROFIT", "PARTIAL_CLOSE"}),
                    "trailing_action_taken": bool(action_types_taken & {"TRAIL_STOP", "TP_PROGRESS_STRUCTURE_STOP", "TRAIL_BY_STRUCTURE", "TRAIL_BY_VOLATILITY"}),
                    "sl_reduced_risk_action_taken": bool(action_types_taken & {"MOVE_SL_TO_REDUCED_RISK"}),
                    "tp_extended": bool(action_types_taken & {"EXTEND_TP"}),
                    "tp_reduced": bool(action_types_taken & {"REDUCE_TP"}),
                }
            )

        dimensions = ("strategy_id", "symbol", "timeframe", "session", "volatility_state", "policy_version")
        grouped: dict[str, dict[str, Any]] = {}
        for dimension in dimensions:
            buckets: dict[str, list[dict[str, Any]]] = {}
            for record in records:
                buckets.setdefault(str(record.get(dimension) or "UNKNOWN"), []).append(record)
            grouped[dimension] = {key: _audit_group_summary(rows) for key, rows in buckets.items()}

        return {
            "session_id": session_id,
            "trade_count": len(records),
            "insufficient_data": len(records) < DEFAULT_MINIMUMS["MIN_TRADES_PER_POLICY_GLOBAL"],
            "minimums": DEFAULT_MINIMUMS,
            "records": records,
            "grouped": grouped,
            "aggregate": _post_trade_aggregate(records),
        }

    def _champion_challenger(self, db: Any, experiment_id: str, rows: list[CounterfactualOutcomeORM]) -> None:
        grouped = _group_outcomes(rows)
        champion = _policy_score("static_baseline_v1", grouped.get("static_baseline_v1", []))
        for policy_id, policy_rows in grouped.items():
            if policy_id == "static_baseline_v1":
                continue
            challenger = _policy_score(policy_id, policy_rows)
            result_id = "CC_" + _hash({"experiment": experiment_id, "policy": policy_id})[:32]
            row = db.get(ChampionChallengerResultORM, result_id) or ChampionChallengerResultORM(result_id=result_id)
            row.experiment_id = experiment_id
            row.champion_policy_id = "static_baseline_v1"
            row.challenger_policy_id = policy_id
            row.sample_size = min(champion["sample_size"], challenger["sample_size"])
            row.champion_score = champion["score"]
            row.challenger_score = challenger["score"]
            row.recommendation = "INSUFFICIENT_DATA" if row.sample_size < DEFAULT_MINIMUMS["MIN_OUT_OF_SAMPLE_TRADES"] else ("PROMOTION_REVIEW_REQUIRED" if challenger["score"] > champion["score"] else "KEEP_CHAMPION")
            row.manual_approval_required = True
            row.scorecard = {"champion": champion, "challenger": challenger}
            db.merge(row)

    def sessions(self) -> list[dict[str, Any]]:
        with SessionLocal() as db:
            return [_orm_dict(row) for row in db.query(AdaptiveSessionORM).order_by(AdaptiveSessionORM.created_at.desc()).limit(100).all()]

    def trades(self) -> list[dict[str, Any]]:
        with SessionLocal() as db:
            return [_orm_dict(row) for row in db.query(AdaptiveTradeEventORM).order_by(AdaptiveTradeEventORM.utc_time.desc()).limit(500).all()]

    def theses(self) -> list[dict[str, Any]]:
        with SessionLocal() as db:
            return [_orm_dict(row) for row in db.query(TradeThesisORM).order_by(TradeThesisORM.updated_at.desc()).limit(500).all()]

    def policies(self) -> list[dict[str, Any]]:
        self.ensure_default_policies()
        with SessionLocal() as db:
            return [_orm_dict(row) for row in db.query(TradeManagementPolicyORM).order_by(TradeManagementPolicyORM.policy_id).all()]

    def experiments(self) -> list[dict[str, Any]]:
        with SessionLocal() as db:
            return [_orm_dict(row) for row in db.query(AdaptiveExperimentORM).order_by(AdaptiveExperimentORM.created_at.desc()).limit(100).all()]

    def shadow_decisions(self) -> list[dict[str, Any]]:
        with SessionLocal() as db:
            return [_orm_dict(row) for row in db.query(ShadowDecisionORM).order_by(ShadowDecisionORM.timestamp.desc()).limit(500).all()]

    async def _monitor_loop(self) -> None:
        interval = _env_int("ADAPTIVE_TRADE_MANAGEMENT_INTERVAL_SECONDS", 10, minimum=2, maximum=60)
        while not self._stop_event.is_set():
            try:
                await self._monitor_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Adaptive trade manager cycle failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def _monitor_cycle(self) -> dict[str, Any]:
        if self._cycle_lock.locked():
            return {"status": "SKIPPED", "reason": "CYCLE_ALREADY_RUNNING"}
        async with self._cycle_lock:
            mode = self.mode()
            if mode == "disabled":
                return {"status": "DISABLED", "broker_mutation_calls": 0}
            await self._ensure_env_activation()
            try:
                positions = await mt5_adapter.mt5_positions()
            except Exception as exc:
                self._open_breaker(f"BROKER_NOT_READY:{exc.__class__.__name__}")
                return {"status": "BROKER_NOT_READY", "error": exc.__class__.__name__, "broker_mutation_calls": 0}
            # Re-fingerprint the connected account every cycle -- an activation approved for a
            # different account (e.g. the previous 100K demo) must never keep managing positions
            # after switching MT5 accounts. None on lookup failure fails safe: _can_execute
            # treats "can't determine the account" the same as "wrong account" (blocked).
            try:
                live_account = await mt5_adapter.mt5_account()
                current_fingerprint = account_registry.fingerprint_account(live_account).fingerprint_hash
            except Exception:
                current_fingerprint = None
            with SessionLocal() as db:
                activation = _active_activation(db)
                breaker = _breaker(db)
                self._auto_recover_breaker(db, breaker)
                currently_open_ids = {_position_id(position.model_dump(mode="json")) for position in positions}
                await self._reconcile_recently_closed(db, currently_open_ids)
                await self._auto_replay_recently_closed(db)
                selected = []
                executed = 0
                for position in positions:
                    payload = position.model_dump(mode="json")
                    symbol = str(payload.get("symbol") or "UNKNOWN").upper()
                    context = await context_for_trade(symbol, utcnow())
                    candles = await _safe_candles(symbol)
                    try:
                        symbol_info = await mt5_adapter.symbol_info(symbol)
                    except Exception:
                        symbol_info = None
                    state = self._sync_position_state(db, payload, activation, context, candles, account_fingerprint=current_fingerprint, symbol_info=symbol_info)
                    try:
                        economic_result = await economic_intelligence_service.evaluate_position(symbol=symbol, direction=state.direction, opened_at=state.opened_at)
                    except Exception as exc:
                        logger.warning("Economic intelligence position evaluation failed: %s", exc.__class__.__name__)
                        economic_result = None
                    candidates = self._evaluate_position(db, state, payload, context, candles, economic_result=economic_result)
                    choice = self._select_action(candidates)
                    action = self._persist_action(db, state, activation, choice, candidates, mode, breaker)
                    selected.append(_orm_dict(action))
                    if self._can_execute(action, state, activation, breaker, mode, current_fingerprint):
                        result = await self._execute_action(action, state, payload)
                        executed += 1 if result.get("broker_mutation_attempted") else 0
                        self._persist_result(db, action, result)
                db.commit()
            return {"status": "OK", "positions": len(positions), "selected_actions": selected, "broker_mutation_calls": executed}

    def _auto_recover_breaker(self, db: Any, breaker: Any) -> None:
        if breaker.state != "open" or not str(breaker.reason or "").startswith("BROKER_NOT_READY"):
            return
        previous_reason = breaker.reason
        breaker.state = "closed"
        breaker.reason = "auto_recovered_broker_reachable"
        breaker.failed_actions = 0
        breaker.rejected_actions = 0
        breaker.reset_at = utcnow()
        breaker.updated_at = utcnow()
        db.merge(breaker)
        db.commit()
        logger.warning("Adaptive circuit breaker auto-recovered: broker reachable again after %s", previous_reason)

    async def _reconcile_recently_closed(self, db: Any, currently_open_ids: set[str]) -> None:
        tracked = db.query(AdaptivePositionStateORM).filter(AdaptivePositionStateORM.closed_detected_at.is_(None), AdaptivePositionStateORM.opened_at.isnot(None)).all()
        newly_closed = [row for row in tracked if row.position_id not in currently_open_ids]
        if not newly_closed:
            return
        now = utcnow()
        for row in newly_closed:
            row.closed_detected_at = now
            db.merge(row)
        db.commit()
        cooldown = _env_int("ADAPTIVE_RECONCILIATION_COOLDOWN_SECONDS", 300, minimum=30, maximum=3600)
        last = _as_aware(self._last_reconciliation_at)
        if last and (now - last).total_seconds() < cooldown:
            return
        self._last_reconciliation_at = now
        try:
            await self.import_mt5_session(days=3)
        except Exception as exc:
            logger.warning("Automatic reconciliation import failed for %d closed position(s): %s", len(newly_closed), exc.__class__.__name__)

    async def _auto_replay_recently_closed(self, db: Any) -> None:
        """Turns the (already-built, otherwise-dormant) replay/counterfactual engine into a
        running pipeline: every closed trade automatically gets simulated against every named
        management policy, exactly once, as soon as its deal data is available. Analysis-only --
        replay()/reconstruct_path() never call the broker and never mutate the original journal
        (see replay()'s own broker_mutation_calls: 0 guarantee). Not gated on newly_closed's
        `closed_detected_at IS NULL` filter (that only fires once per position and would
        otherwise silently drop trades whose deal data wasn't backfilled yet on the first pass);
        gated on replay_completed_at instead so a trade is retried on later cycles until its
        deals actually exist."""
        now = utcnow()
        cooldown = _env_int("ADAPTIVE_AUTO_REPLAY_COOLDOWN_SECONDS", 60, minimum=10, maximum=3600)
        last = _as_aware(self._last_auto_replay_at)
        if last and (now - last).total_seconds() < cooldown:
            return
        self._last_auto_replay_at = now
        pending = (
            db.query(AdaptivePositionStateORM)
            .filter(AdaptivePositionStateORM.closed_detected_at.isnot(None), AdaptivePositionStateORM.replay_completed_at.is_(None))
            .order_by(AdaptivePositionStateORM.closed_detected_at.asc())
            .limit(_env_int("ADAPTIVE_AUTO_REPLAY_BATCH_SIZE", 5, minimum=1, maximum=50))
            .all()
        )
        for row in pending:
            try:
                deals = db.query(AdaptiveTradeEventORM).filter(AdaptiveTradeEventORM.position_id == row.position_id, AdaptiveTradeEventORM.event_type == "DEAL").all()
                if not deals:
                    continue
                exit_deal = max(deals, key=lambda d: d.utc_time or row.closed_detected_at)
                realized_pnl = sum(float(d.realized_pnl or 0) + float(d.commission or 0) + float(d.swap or 0) + float(d.fee or 0) for d in deals)
                trade = {
                    "trade_id": row.position_id,
                    "symbol": row.symbol,
                    "direction": row.direction,
                    "volume": row.original_volume,
                    "entry": row.entry_price,
                    "stop_loss": row.original_sl,
                    "take_profit": row.original_tp,
                    "entry_time": row.opened_at,
                    "exit_time": exit_deal.utc_time or row.closed_detected_at,
                    "actual_pnl": realized_pnl,
                    "strategy_id": row.strategy_id,
                    "timeframe": row.timeframe,
                }
                candles = await _replay_candles(row.symbol, row.timeframe or "M5", row.opened_at, trade["exit_time"])
                if not candles:
                    continue
                self.replay(trade, candles, policy_ids=None)
                row.replay_completed_at = now
                db.merge(row)
                db.commit()
            except Exception as exc:
                logger.warning("Automatic replay failed for closed position %s: %s", row.position_id, exc.__class__.__name__)

    def _reconcile_sltp_confirmations(self, db: Any, position_id: str, current_sl: float | None, current_tp: float | None) -> None:
        pending = (
            db.query(AdaptiveBrokerActionResultORM, AdaptiveManagementActionORM)
            .join(AdaptiveManagementActionORM, AdaptiveBrokerActionResultORM.action_id == AdaptiveManagementActionORM.action_id)
            .filter(
                AdaptiveBrokerActionResultORM.reconciliation_state == "pending",
                AdaptiveManagementActionORM.position_id == position_id,
                AdaptiveManagementActionORM.action_type.in_(SLTP_MODIFY_ACTION_TYPES),
            )
            .all()
        )
        for result_row, action_row in pending:
            result_row.confirmed_sl = current_sl
            result_row.confirmed_tp = current_tp
            sl_matches = _price_matches(action_row.requested_sl, current_sl)
            tp_matches = _price_matches(action_row.requested_tp, current_tp)
            result_row.reconciliation_state = "confirmed" if sl_matches and tp_matches else "mismatch"
            db.merge(result_row)

    def _sync_position_state(self, db: Any, payload: dict[str, Any], activation: Any | None, context: dict[str, Any], candles: list[dict[str, Any]], account_fingerprint: str | None = None, symbol_info: Any | None = None) -> AdaptivePositionStateORM:
        position_id = _position_id(payload)
        row = db.get(AdaptivePositionStateORM, position_id) or AdaptivePositionStateORM(position_id=position_id)
        direction = _position_direction(payload)
        opened_at = _position_time(payload)
        current_price = float(payload.get("price_current") or payload.get("price_open") or 0)
        entry = float(payload.get("price_open") or current_price or 0)
        sl = _float(payload.get("sl"))
        tp = _float(payload.get("tp"))
        self._reconcile_sltp_confirmations(db, position_id, sl, tp)
        risk = abs(entry - sl) if sl else 0.00001
        r_now = _signed_r(TradeCase(position_id, None, str(payload.get("symbol")).upper(), direction, float(payload.get("volume") or 0), entry, sl or entry, tp or entry, opened_at or utcnow(), None, 0), current_price, risk)
        row.activation_id = activation.activation_id if activation else row.activation_id
        if account_fingerprint:
            row.account_fingerprint = account_fingerprint
        row.symbol = str(payload.get("symbol") or "UNKNOWN").upper()
        row.direction = direction
        row.broker_ticket = str(payload.get("ticket") or payload.get("identifier") or position_id)
        row.thesis_id = _lineage(payload.get("comment"), "setup")
        row.opened_at = opened_at
        row.original_volume = row.original_volume or float(payload.get("volume") or 0)
        row.current_volume = float(payload.get("volume") or 0)
        row.entry_price = entry
        is_first_sight = row.original_sl is None
        row.original_sl = row.original_sl if row.original_sl is not None else sl
        row.current_sl = sl
        row.original_tp = row.original_tp if row.original_tp is not None else tp
        row.current_tp = tp
        if not row.strategy_id or row.strategy_id == "UNKNOWN":
            row.strategy_id = _lineage(payload.get("comment"), "strategy")
        if not row.timeframe or row.timeframe == "UNKNOWN":
            row.timeframe = _lineage(payload.get("comment"), "timeframe")
        row.max_achieved_r = max(float(row.max_achieved_r or 0), r_now)
        row.min_achieved_r = min(float(row.min_achieved_r or 0), r_now)
        if r_now >= row.max_achieved_r:
            row.mfe_price = current_price
        if r_now <= row.min_achieved_r:
            row.mae_price = current_price
        row.adopted = bool(row.adopted or _is_adopted(db, position_id))
        row.managed_automatically = _existing_allowed(opened_at, activation) or row.adopted

        regime_info = detect_regime(candles, context=context)
        atr = float(regime_info.get("features", {}).get("atr") or 0) or None
        progress = tp_protection.tp_progress(direction, entry, current_price, tp)
        row.tp_progress = progress if progress is not None else float(row.tp_progress or 0)
        if progress is not None and progress > float(row.max_tp_progress or 0):
            row.max_tp_progress = progress
            row.max_tp_progress_at = utcnow()
        row.current_giveback_r = max(0.0, float(row.max_achieved_r or 0) - r_now)
        atr_r = (atr / risk) if atr and risk else 0.0
        allowance_fraction = tp_protection.retracement_allowance(atr_r=atr_r, regime=regime_info.get("regime", "insufficient_data"), timeframe=os.getenv("ADAPTIVE_MANAGEMENT_TIMEFRAME", "M5"))
        allowance_r = allowance_fraction * float(row.max_achieved_r or 0)
        retracement_state = tp_protection.classify_retracement(row.current_giveback_r, allowance_r)
        candles_held = _candles_held(opened_at, candles) if (opened_at and candles) else None
        winner = tp_protection.classify_winner_preservation(
            {
                "opposing_candles": _opposing_candles(candles, direction),
                "retracement_state": retracement_state,
                "regime": regime_info.get("regime", "insufficient_data"),
                "direction": direction,
                "candles_held": candles_held,
                "remaining_reward_r": (1.0 - progress) if progress is not None else None,
            }
        )
        row.winner_classification = winner["classification"]
        floor = tp_protection.resolve_profit_lock_floor(max_tp_progress=float(row.max_tp_progress or 0), max_achieved_r=float(row.max_achieved_r or 0), regime=regime_info.get("regime", "insufficient_data"), atr_r=atr_r)
        row.profit_lock_floor_r = floor["floor_r"]

        row.raw_payload = sanitize(payload)
        row.updated_at = utcnow()
        db.merge(row)
        if is_first_sight and row.original_sl is not None:
            self._audit_initial_stop(db, row, payload, candles, atr, symbol_info)
        return row

    def _audit_initial_stop(self, db: Any, row: AdaptivePositionStateORM, payload: dict[str, Any], candles: list[dict[str, Any]], atr: float | None, symbol_info: Any | None = None) -> None:
        existing = db.query(AdaptiveStopQualityAuditORM).filter(AdaptiveStopQualityAuditORM.position_id == row.position_id).first()
        if existing:
            return
        sl_distance = abs(float(row.entry_price or 0) - float(row.original_sl or row.entry_price or 0))
        normalized = [candle for candle in (_normalize_candle(item) for item in candles) if candle]
        spread = normalized[-1]["spread"] if normalized and normalized[-1].get("spread") is not None else None
        structure_level = _swing_structure_level(normalized, row.direction)
        structure_distance = abs(float(row.entry_price or 0) - structure_level) if structure_level is not None else None
        # Real broker minimum stop distance (points -> price), fixing a prior bug where this
        # was hardcoded to None and the "stop too close to the broker's minimum" check could
        # never actually fire.
        broker_min_stop_distance: float | None = None
        if symbol_info is not None:
            stops_level = float(getattr(symbol_info, "trade_stops_level", None) or 0)
            point = float(getattr(symbol_info, "point", None) or 0)
            if stops_level and point:
                broker_min_stop_distance = stops_level * point
        result = tp_protection.classify_stop_quality(sl_distance=sl_distance, atr=atr, spread=spread, structure_distance=structure_distance, broker_min_stop=broker_min_stop_distance)
        # SPREAD_TO_STOP_MAX_RATIO is spread-as-a-fraction-of-stop (e.g. 0.20 = spread must be
        # <=20% of the stop distance); classify_stop_quality_v2's min_spread_ratio is its
        # reciprocal (stop must be >= spread * min_spread_ratio) -- same constraint, inverted form.
        spread_to_stop_max_ratio = _env_float("SPREAD_TO_STOP_MAX_RATIO", 1.0 / 3.0)
        result_v2 = tp_protection.classify_stop_quality_v2(
            sl_distance=sl_distance,
            atr=atr,
            spread=spread,
            structure_distance=structure_distance,
            broker_min_stop_distance=broker_min_stop_distance,
            min_atr_mult=_env_float("ATR_STOP_MULTIPLIER_MIN", 0.8),
            min_spread_ratio=(1.0 / spread_to_stop_max_ratio) if spread_to_stop_max_ratio > 0 else 3.0,
        )
        audit = AdaptiveStopQualityAuditORM(audit_id="ASQ_" + _hash({"position": row.position_id})[:40])
        audit.position_id = row.position_id
        audit.symbol = row.symbol
        audit.strategy_id = _lineage(payload.get("comment"), "strategy")
        audit.timeframe = _lineage(payload.get("comment"), "timeframe")
        audit.direction = row.direction
        audit.sl_distance_price = sl_distance
        audit.sl_atr_multiple = result["sl_atr_multiple"]
        audit.spread_pct_of_sl = result["spread_pct_of_sl"]
        audit.structure_buffer_price = structure_distance
        audit.broker_min_stop_price = broker_min_stop_distance
        audit.flags = result["flags"]
        audit.classification = result["classification"]
        audit.classification_v2 = result_v2["classification"]
        audit.raw_payload = sanitize({"entry": row.entry_price, "sl": row.original_sl, "atr": atr})
        db.merge(audit)
        row.stop_quality_classification = result["classification"]
        row.stop_quality_v2 = result_v2["classification"]

    def _evaluate_position(self, db: Any, state: AdaptivePositionStateORM, payload: dict[str, Any], context: dict[str, Any], candles: list[dict[str, Any]], economic_result: dict[str, Any] | None = None) -> list[ManagementCandidate]:
        entry = float(state.entry_price or 0)
        price = float(payload.get("price_current") or entry)
        sl = float(state.current_sl or state.original_sl or entry)
        risk = abs(entry - sl) or 0.00001
        case = TradeCase(state.position_id, None, state.symbol, state.direction, float(state.current_volume or 0), entry, sl, float(state.current_tp or entry), state.opened_at or utcnow(), None, 0)
        r_now = _signed_r(case, price, risk)
        candidates = [ManagementCandidate("HOLD", 100, reason="no_management_trigger", evidence={"r": r_now, "max_r": state.max_achieved_r, "tp_progress": state.tp_progress, "winner_classification": state.winner_classification})]
        if not state.current_sl or not state.current_tp:
            candidates.append(ManagementCandidate("HOLD", 1, reason="missing_static_protection_preserve_manual_review", evidence={"sl": state.current_sl, "tp": state.current_tp}))
            return candidates
        if _opposing_candles(candles, state.direction) >= 2 and r_now < -0.25:
            candidates.append(ManagementCandidate("THESIS_INVALIDATION_CLOSE", 10, requested_volume=float(state.current_volume), reason="two_completed_opposing_candles_after_adverse_move", evidence={"r": r_now}))
        event_risk = str(context.get("scheduled_event_risk") or context.get("headline_risk") or "").lower()
        if ("high" in event_risk or "elevated" in event_risk) and r_now > 0.2:
            candidates.append(ManagementCandidate("EVENT_RISK_REDUCTION", 20, requested_volume=float(state.current_volume) * 0.25, reason="elevated_event_risk_reduce_exposure", evidence={"r": r_now, "context": context}))

        economic_decision = str(((economic_result or {}).get("guard") or {}).get("decision") or "ALLOW").upper()
        manage_existing_only = economic_decision in {"MANAGE_EXISTING_ONLY", "BLOCK"}
        if economic_decision == "REDUCE_SIZE" and r_now > 0:
            # Priority 21: same tier as the existing EVENT_RISK_REDUCTION(20) -- deliberately
            # ranked below THESIS_INVALIDATION_CLOSE(10) and above the TP-progress protection
            # ladder (25/30/35), matching how EVENT_RISK_REDUCTION already ranks in this file.
            size_multiplier = float(((economic_result or {}).get("guard") or {}).get("size_multiplier") or 0.5)
            candidates.append(ManagementCandidate("ECONOMIC_REDUCE_SIZE", 21, requested_volume=float(state.current_volume) * (1 - size_multiplier), reason="forex_factory_calendar_event_reduce_exposure", evidence={"r": r_now, "economic_guard": economic_result}))
        if manage_existing_only:
            # Priority 95: a pure "no nonessential SL/TP changes" advisory marker -- it must
            # never outrank a real protective/closing action (THESIS_INVALIDATION_CLOSE,
            # EVENT_RISK_REDUCTION, TP_PROGRESS_PROFIT_LOCK, MFE_PROTECTION_CLOSE,
            # TP_PROGRESS_PARTIAL_PROTECT, PARTIAL_PROFIT, TIME_EXIT all still fire normally).
            # The actual suppression of MOVE_SL_BREAKEVEN/TRAIL_STOP/TP_PROGRESS_STRUCTURE_STOP
            # happens below via the `manage_existing_only` flag directly, not via priority.
            candidates.append(ManagementCandidate("ECONOMIC_MANAGE_EXISTING_ONLY", 95, reason="forex_factory_calendar_manage_existing_only", evidence={"r": r_now, "economic_guard": economic_result}))

        max_r = float(state.max_achieved_r or 0)
        regime_info = detect_regime(candles, context=context)
        regime = str(regime_info.get("regime", "insufficient_data"))
        atr = float(regime_info.get("features", {}).get("atr") or 0) or None
        atr_r = (atr / risk) if atr else 0.0
        timeframe = os.getenv("ADAPTIVE_MANAGEMENT_TIMEFRAME", "M5")
        normalized_candles = [row for row in (_normalize_candle(item) for item in candles) if row]
        cooldown_ok = _cooldown_elapsed(state.last_management_at)

        floor = tp_protection.resolve_profit_lock_floor(max_tp_progress=float(state.max_tp_progress or 0), max_achieved_r=max_r, regime=regime, atr_r=atr_r)
        if floor["triggered"] and r_now < float(floor["floor_r"] or 0):
            action_kind, fraction = _resolve_profit_lock_action(state.winner_classification, float(state.max_tp_progress or 0))
            candidates.append(
                ManagementCandidate(
                    "TP_PROGRESS_PROFIT_LOCK",
                    25,
                    requested_volume=float(state.current_volume) * (1.0 if action_kind == "FULL" else fraction),
                    reason=f"profit_lock_floor_breach_{state.winner_classification}",
                    evidence={"r": r_now, "max_r": max_r, "max_tp_progress": state.max_tp_progress, "floor_r": floor["floor_r"], "protect_fraction": floor["protect_fraction"], "winner_classification": state.winner_classification, "resolved_action": action_kind},
                )
            )

        giveback_r = max(0.0, max_r - r_now)
        allowance_fraction = tp_protection.retracement_allowance(atr_r=atr_r, regime=regime, timeframe=timeframe)
        # Additive ceiling on top of the existing regime-aware allowance: once a trade has
        # actually reached 1.0R/1.5R of favorable movement, giveback tolerance is capped at the
        # configured ratio, never loosened. No effect below 1.0R (byte-identical to before).
        if max_r >= 1.5:
            allowance_fraction = min(allowance_fraction, _env_float("MFE_GIVEBACK_LIMIT_AFTER_1_5R", 0.35))
        elif max_r >= 1.0:
            allowance_fraction = min(allowance_fraction, _env_float("MFE_GIVEBACK_LIMIT_AFTER_1R", 0.50))
        allowance_r = allowance_fraction * max_r
        if max_r >= _env_float("ADAPTIVE_MFE_MIN_R", 0.5) and giveback_r > allowance_r:
            candidates.append(
                ManagementCandidate(
                    "MFE_PROTECTION_CLOSE",
                    30,
                    requested_volume=float(state.current_volume),
                    reason="profit_giveback_exceeds_volatility_aware_allowance",
                    evidence={"r": r_now, "max_r": max_r, "giveback_r": giveback_r, "allowance_r": allowance_r, "allowance_fraction": allowance_fraction, "regime": regime},
                )
            )

        zone = tp_protection.progress_zone(state.tp_progress)
        zone_stage = {"zone_60_75": "stage_1", "zone_75_85": "stage_1", "zone_85_95": "stage_2", "zone_95_plus": "runner"}.get(zone)
        if zone_stage and state.winner_classification in {"weakening", "critical"} and not _stage_already_executed(db, state.position_id, zone_stage):
            fraction = {"stage_1": _env_float("ADAPTIVE_TP_STAGE1_FRACTION", 0.25), "stage_2": _env_float("ADAPTIVE_TP_STAGE2_FRACTION", 0.30), "runner": _env_float("ADAPTIVE_TP_RUNNER_FRACTION", 0.5)}[zone_stage]
            candidates.append(
                ManagementCandidate(
                    "TP_PROGRESS_PARTIAL_PROTECT",
                    35,
                    requested_volume=float(state.current_volume) * fraction,
                    reason=f"tp_progress_zone_{zone}_weakening_protect_{zone_stage}",
                    evidence={"tp_progress": state.tp_progress, "zone": zone, "stage": zone_stage, "requested_fraction": fraction, "winner_classification": state.winner_classification},
                )
            )

        if r_now >= _env_float("ADAPTIVE_PARTIAL_PROFIT_R", 0.5):
            candidates.append(ManagementCandidate("PARTIAL_PROFIT", 40, requested_volume=float(state.current_volume) * _env_float("ADAPTIVE_PARTIAL_PROFIT_FRACTION", 0.25), reason="partial_profit_threshold", evidence={"r": r_now}))

        if zone in {"zone_75_85", "zone_85_95", "zone_95_plus"} and state.winner_classification in {"strong_continuation", "healthy_pullback"} and cooldown_ok and not manage_existing_only:
            structure_level = _swing_structure_level(normalized_candles, state.direction)
            spread = regime_info.get("features", {}).get("spread")
            protective = tp_protection.construct_dynamic_stop(
                state.direction, price, structure_level, atr, spread,
                min_atr_mult=_env_float("ADAPTIVE_PROTECT_MIN_ATR_MULT", 0.3),
                max_atr_mult=_env_float("ADAPTIVE_PROTECT_MAX_ATR_MULT", 1.5),
                min_spread_ratio=_env_float("ADAPTIVE_PROTECT_MIN_SPREAD_RATIO", 2.0),
            )
            if protective and _stop_improves(state.direction, protective, state.current_sl):
                candidates.append(
                    ManagementCandidate(
                        "TP_PROGRESS_STRUCTURE_STOP",
                        45,
                        requested_sl=protective,
                        requested_tp=state.current_tp,
                        reason=f"tp_progress_zone_{zone}_structure_protective_stop",
                        evidence={"tp_progress": state.tp_progress, "zone": zone, "winner_classification": state.winner_classification},
                    )
                )

        if cooldown_ok and not manage_existing_only:
            be = _breakeven_price(state)
            structure_level = _swing_structure_level(normalized_candles, state.direction)
            be_candidate = _structure_preferred_breakeven(state.direction, be, structure_level, atr)
            breakeven_ready = (
                r_now >= _env_float("ADAPTIVE_BREAKEVEN_R", 1.0)
                and float(state.tp_progress or 0) >= _env_float("ADAPTIVE_BREAKEVEN_MIN_TP_PROGRESS", 0.3)
                and state.winner_classification != "invalidated"
                and _opposing_candles(candles, state.direction) < 2
                and atr_r < _env_float("ADAPTIVE_BREAKEVEN_MAX_ATR_R", 4.0)
                and _stop_improves(state.direction, be_candidate, state.current_sl)
            )
            if breakeven_ready:
                candidates.append(
                    ManagementCandidate(
                        "MOVE_SL_BREAKEVEN",
                        50,
                        requested_sl=be_candidate,
                        requested_tp=state.current_tp,
                        reason="protect_cost_adjusted_or_structure_breakeven",
                        evidence={"r": r_now, "tp_progress": state.tp_progress, "structure_based": be_candidate != be},
                    )
                )

            trail_fraction = tp_protection.retracement_allowance(atr_r=atr_r, regime=regime, timeframe=timeframe, min_fraction=0.2, max_fraction=0.5)
            trail = _trail_stop(state, price, trail_fraction)
            if r_now >= _env_float("ADAPTIVE_TRAIL_R", 1.5) and trail and _stop_improves(state.direction, trail, state.current_sl):
                candidates.append(ManagementCandidate("TRAIL_STOP", 60, requested_sl=trail, requested_tp=state.current_tp, reason="volatility_aware_trailing_stop", evidence={"r": r_now, "max_r": max_r, "trail_fraction": trail_fraction}))

        if _candles_held(state.opened_at, candles) >= _env_int("ADAPTIVE_TIME_EXIT_CANDLES", 24, minimum=1, maximum=288) and max_r < 0.25:
            candidates.append(ManagementCandidate("TIME_EXIT", 70, requested_volume=float(state.current_volume), reason="no_progress_time_exit", evidence={"max_r": max_r}))

        if v2_mode() != "disabled" and cooldown_ok and not manage_existing_only:
            candidates.extend(self._v2_candidates(state, r_now, max_r, atr, atr_r, regime, zone, normalized_candles))
        return candidates

    def _v2_candidates(self, state: AdaptivePositionStateORM, r_now: float, max_r: float, atr: float | None, atr_r: float, regime: str, zone: str, normalized_candles: list[dict[str, Any]]) -> list[ManagementCandidate]:
        """v2 action types (MOVE_SL_TO_REDUCED_RISK/EXTEND_TP/REDUCE_TP), gated by their own
        v2_mode() shadow/enforce switch independent of the existing demo_active gate -- see
        V2_ACTION_TYPES and the module docstring above it."""
        out: list[ManagementCandidate] = []
        entry = float(state.entry_price or 0)

        reduced_risk_r = _env_float("ADAPTIVE_REDUCED_RISK_R", 0.5)
        breakeven_r = _env_float("ADAPTIVE_BREAKEVEN_R", 1.0)
        if reduced_risk_r <= r_now < breakeven_r and state.winner_classification not in {"invalidated", "critical"} and state.current_sl is not None:
            halfway = entry - (entry - float(state.current_sl)) / 2.0 if state.direction == "LONG" else entry + (float(state.current_sl) - entry) / 2.0
            if _stop_improves(state.direction, halfway, state.current_sl):
                out.append(
                    ManagementCandidate(
                        "MOVE_SL_TO_REDUCED_RISK",
                        49,
                        requested_sl=halfway,
                        requested_tp=state.current_tp,
                        reason="reduced_risk_step_before_full_breakeven_eligibility",
                        evidence={"r": r_now, "reduced_risk_r_threshold": reduced_risk_r, "breakeven_r_threshold": breakeven_r, "winner_classification": state.winner_classification},
                    )
                )

        sl_at_or_beyond_breakeven = state.current_sl is not None and ((state.direction == "LONG" and float(state.current_sl) >= entry) or (state.direction == "SHORT" and float(state.current_sl) <= entry))
        if zone in {"zone_85_95", "zone_95_plus"} and state.winner_classification in {"strong_continuation", "healthy_pullback"} and sl_at_or_beyond_breakeven and state.current_tp and atr:
            extension = atr * _env_float("ADAPTIVE_TP_EXTEND_ATR_MULT", 1.0)
            new_tp = float(state.current_tp) + extension if state.direction == "LONG" else float(state.current_tp) - extension
            out.append(
                ManagementCandidate(
                    "EXTEND_TP",
                    65,
                    requested_sl=state.current_sl,
                    requested_tp=new_tp,
                    reason="momentum_and_structure_continuation_extend_target",
                    evidence={"zone": zone, "winner_classification": state.winner_classification, "previous_tp": state.current_tp, "proposed_tp": new_tp, "profit_protected": sl_at_or_beyond_breakeven},
                )
            )

        if state.winner_classification in {"weakening", "critical"} and float(state.tp_progress or 0) >= _env_float("ADAPTIVE_TP_REDUCE_MIN_PROGRESS", 0.5) and state.current_tp:
            price_now = entry + r_now * abs(entry - float(state.current_sl or entry)) if state.direction == "LONG" else entry - r_now * abs(entry - float(state.current_sl or entry))
            new_tp = price_now + (float(state.current_tp) - price_now) * (1.0 - _env_float("ADAPTIVE_TP_REDUCE_FRACTION", 0.35))
            tp_moves_closer = abs(new_tp - price_now) < abs(float(state.current_tp) - price_now)
            if tp_moves_closer:
                out.append(
                    ManagementCandidate(
                        "REDUCE_TP",
                        42,
                        requested_sl=state.current_sl,
                        requested_tp=new_tp,
                        reason="momentum_deteriorating_original_target_statistically_unrealistic",
                        evidence={"tp_progress": state.tp_progress, "winner_classification": state.winner_classification, "previous_tp": state.current_tp, "proposed_tp": new_tp},
                    )
                )
        return out

    def _select_action(self, candidates: list[ManagementCandidate]) -> ManagementCandidate:
        return sorted(candidates, key=lambda row: row.priority)[0]

    def _persist_action(self, db: Any, state: AdaptivePositionStateORM, activation: Any | None, choice: ManagementCandidate, candidates: list[ManagementCandidate], mode: str, breaker: Any) -> AdaptiveManagementActionORM:
        key = _idempotency_key(state.position_id, choice.action_type, choice.requested_volume, choice.requested_sl, choice.requested_tp)
        existing = db.query(AdaptiveManagementActionORM).filter(AdaptiveManagementActionORM.idempotency_key == key).first()
        if existing:
            return existing
        action = AdaptiveManagementActionORM(action_id="AMA_" + _hash({"key": key})[:40])
        action.activation_id = activation.activation_id if activation else None
        action.position_id = state.position_id
        action.thesis_id = state.thesis_id
        action.policy_id = ACTIVE_POLICY_ID
        action.action_type = choice.action_type
        action.priority = choice.priority
        action.mode = mode
        action.status = "selected" if choice.action_type != "HOLD" else "hold"
        action.idempotency_key = key
        action.requested_volume = choice.requested_volume
        action.requested_sl = choice.requested_sl
        action.requested_tp = choice.requested_tp
        action.requested_price = choice.requested_price
        action.selected = True
        action.considered_actions = [candidate.__dict__ for candidate in candidates]
        action.evidence = sanitize(choice.evidence or {"reason": choice.reason})
        action.broker_mutation_attempted = False
        if mode != "demo_active":
            action.status = "shadow_selected"
        elif not activation:
            action.status = "shadow_no_activation"
        elif breaker.state != "closed":
            action.status = "blocked_circuit_open"
        elif not state.managed_automatically:
            action.status = "blocked_not_adopted"
        return db.merge(action)

    def _can_execute(self, action: AdaptiveManagementActionORM, state: AdaptivePositionStateORM, activation: Any | None, breaker: Any, mode: str, current_account_fingerprint: str | None = None) -> bool:
        cfg = mt5_config()
        if action.action_type == "HOLD" or mode != "demo_active" or not activation or breaker.state != "closed":
            return False
        if action.broker_mutation_attempted or action.status in {"submitted", "rejected", "error"}:
            return False
        if action.policy_id != ACTIVE_POLICY_ID or cfg.live_trading_enabled or cfg.account_mode != "DEMO":
            return False
        if not state.managed_automatically:
            return False
        # Account-identity re-verification: an activation approved for a different MT5 account
        # (fingerprint mismatch) or an undetermined current account (lookup failure -- fail
        # closed, matches "unknown account blocked") must never execute. Only enforced when the
        # activation actually has a fingerprint recorded, so legacy/test activations created
        # before this column existed aren't newly broken.
        if activation.account_fingerprint and activation.account_fingerprint != current_account_fingerprint:
            return False
        if action.action_type in V2_ACTION_TYPES and v2_mode() != "enforce":
            return False
        if not _rate_limit_ok(breaker, activation.maximum_actions_per_hour):
            return False
        symbols = [str(row).upper() for row in (activation.eligible_symbols or [])]
        return not symbols or state.symbol in symbols

    async def _execute_action(self, action: AdaptiveManagementActionORM, state: AdaptivePositionStateORM, payload: dict[str, Any]) -> dict[str, Any]:
        if action.policy_id != ACTIVE_POLICY_ID:
            return {"status": "REJECTED", "reason": "ONLY_CONSERVATIVE_DEMO_MANAGER_CAN_EXECUTE", "broker_mutation_attempted": False}
        try:
            mt5 = mt5_adapter.client.ensure_ready()
            symbol = await mt5_adapter.symbol_info(state.symbol)
            quote = await mt5_adapter.latest_tick(state.symbol)
            request = await self._build_mt5_request(mt5, symbol, quote, action, state, payload)
            if not request:
                return {"status": "NOOP", "broker_mutation_attempted": False}
            action.broker_mutation_attempted = True
            raw = await execution_manager.submit_mt5_request(
                adapter=mt5_adapter,
                request=request,
                idempotency_key=action.idempotency_key,
                source="adaptive_trade_manager",
                expected_price=float(request.get("price")) if request.get("price") is not None else None,
            )
            data = _asdict(raw)
            retcode = data.get("retcode")
            ok = retcode in {getattr(mt5, "TRADE_RETCODE_DONE", None), getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", None), getattr(mt5, "TRADE_RETCODE_PLACED", None)}
            if not ok:
                self._record_action_failure(f"MT5_REJECTED:{retcode}")
            return {"status": "ACCEPTED" if ok else "REJECTED", "broker_mutation_attempted": True, "retcode": retcode, "broker_ticket": data.get("order") or data.get("deal"), "fill_price": data.get("price"), "filled_volume": data.get("volume"), "raw_request": sanitize(request), "raw_response": sanitize(data)}
        except Exception as exc:
            self._record_action_failure(exc.__class__.__name__)
            return {"status": "ERROR", "reason": exc.__class__.__name__, "broker_mutation_attempted": False}

    async def _build_mt5_request(self, mt5: Any, symbol: Any, quote: Any, action: AdaptiveManagementActionORM, state: AdaptivePositionStateORM, payload: dict[str, Any]) -> dict[str, Any] | None:
        if action.action_type in {"PARTIAL_PROFIT", "EVENT_RISK_REDUCTION", "MFE_PROTECTION_CLOSE", "TIME_EXIT", "THESIS_INVALIDATION_CLOSE", "TP_PROGRESS_PROFIT_LOCK", "TP_PROGRESS_PARTIAL_PROTECT", "ECONOMIC_REDUCE_SIZE"}:
            volume = _normalize_volume(action.requested_volume or state.current_volume, symbol)
            if volume <= 0:
                return None
            order_type = getattr(mt5, "ORDER_TYPE_SELL", 1) if state.direction == "LONG" else getattr(mt5, "ORDER_TYPE_BUY", 0)
            price = float(quote.bid if state.direction == "LONG" else quote.ask)
            filling_type = self._management_filling_type(mt5, symbol)
            return {
                "action": getattr(mt5, "TRADE_ACTION_DEAL", 1),
                "position": int(payload.get("ticket") or payload.get("identifier") or state.broker_ticket),
                "symbol": state.symbol,
                "volume": volume,
                "type": order_type,
                "price": price,
                "deviation": _env_int("ADAPTIVE_MT5_DEVIATION", 20, minimum=1, maximum=100),
                "magic": mt5_config().bensim_magic,
                "comment": _bsm_comment(state.strategy_id, state.timeframe, "EXIT"),
                "type_time": getattr(mt5, "ORDER_TIME_GTC", 0),
                "type_filling": filling_type,
            }
        if action.action_type in SLTP_MODIFY_ACTION_TYPES:
            return {
                "action": getattr(mt5, "TRADE_ACTION_SLTP", 6),
                "position": int(payload.get("ticket") or payload.get("identifier") or state.broker_ticket),
                "symbol": state.symbol,
                "sl": _round_price(action.requested_sl, symbol) if action.requested_sl else _round_price(state.current_sl, symbol),
                "tp": _round_price(action.requested_tp, symbol) if action.requested_tp else 0.0,
                "magic": mt5_config().bensim_magic,
                "comment": _bsm_comment(state.strategy_id, state.timeframe, "SLTP"),
            }
        return None

    def _management_filling_type(self, mt5: Any, symbol: Any) -> int:
        flags = int(getattr(symbol, "filling_mode", 0) or 0)
        if flags & 1:
            return int(getattr(mt5, "ORDER_FILLING_FOK", 0))
        if flags & 2:
            return int(getattr(mt5, "ORDER_FILLING_IOC", 1))
        return int(getattr(mt5, "ORDER_FILLING_RETURN", getattr(mt5, "ORDER_FILLING_IOC", 1)))

    def _persist_result(self, db: Any, action: AdaptiveManagementActionORM, result: dict[str, Any]) -> None:
        result_id = "AMR_" + _hash({"action": action.action_id, "result": result, "time": utcnow().isoformat()})[:40]
        row = AdaptiveBrokerActionResultORM(result_id=result_id)
        row.action_id = action.action_id
        row.broker_ticket = str(result.get("broker_ticket") or "") or None
        row.retcode = _int(result.get("retcode"))
        row.status = result.get("status") or "UNKNOWN"
        row.fill_price = _float(result.get("fill_price"))
        row.filled_volume = _float(result.get("filled_volume"))
        row.raw_request = sanitize(result.get("raw_request") or {})
        row.raw_response = sanitize(result.get("raw_response") or result)
        row.reconciliation_state = "pending" if result.get("broker_mutation_attempted") else "not_attempted"
        action.status = "submitted" if result.get("status") == "ACCEPTED" else str(result.get("status") or "error").lower()
        action.updated_at = utcnow()
        db.merge(row)
        if result.get("status") == "ACCEPTED":
            state = db.get(AdaptivePositionStateORM, action.position_id)
            if state and action.action_type in SLTP_MODIFY_ACTION_TYPES:
                state.last_management_at = utcnow()
                db.merge(state)
            stage = (action.evidence or {}).get("stage")
            if state and action.action_type == "TP_PROGRESS_PARTIAL_PROTECT" and stage:
                stage_row = AdaptivePartialExitStageORM(stage_id="APES_" + _hash({"position": action.position_id, "stage": stage})[:40])
                stage_row.position_id = action.position_id
                stage_row.stage = stage
                stage_row.trigger_tp_progress = float((action.evidence or {}).get("tp_progress") or 0)
                stage_row.requested_fraction = float((action.evidence or {}).get("requested_fraction") or 0)
                stage_row.executed_volume = _float(result.get("filled_volume"))
                stage_row.action_id = action.action_id
                db.merge(stage_row)

    def _record_action_failure(self, reason: str) -> None:
        with SessionLocal() as db:
            breaker = _breaker(db)
            breaker.failed_actions += 1
            breaker.reason = reason
            if breaker.failed_actions >= _env_int("ADAPTIVE_BREAKER_FAILURE_LIMIT", 3, minimum=1, maximum=10):
                breaker.state = "open"
                breaker.opened_at = utcnow()
            db.merge(breaker)
            db.commit()

    def _open_breaker(self, reason: str) -> None:
        with SessionLocal() as db:
            breaker = _breaker(db)
            breaker.state = "open"
            breaker.reason = reason
            breaker.opened_at = utcnow()
            db.merge(breaker)
            db.commit()


def reconstruct_path(case: TradeCase, candles: list[dict[str, Any]], context: dict[str, Any] | None = None) -> dict[str, Any]:
    ordered: list[dict[str, Any]] = []
    for row in candles:
        candle = _normalize_candle(row)
        if candle and candle["time"] >= case.entry_time and (case.exit_time is None or candle["time"] <= case.exit_time):
            ordered.append(candle)
    risk_price = abs(case.entry - case.stop_loss) or 0.00001
    target_r = abs(case.take_profit - case.entry) / risk_price
    timeline: list[dict[str, Any]] = []
    mfe = 0.0
    mae = 0.0
    mfe_time: datetime | None = None
    mae_time: datetime | None = None
    spreads: list[float] = []
    closes: list[float] = []
    previous_regime: str | None = None
    regimes: list[dict[str, Any]] = []
    for index, candle in enumerate(ordered, start=1):
        favourable_price = candle["high"] if case.direction == "LONG" else candle["low"]
        adverse_price = candle["low"] if case.direction == "LONG" else candle["high"]
        favourable_r = _signed_r(case, favourable_price, risk_price)
        adverse_r = _signed_r(case, adverse_price, risk_price)
        close_r = _signed_r(case, candle["close"], risk_price)
        if favourable_r > mfe:
            mfe = favourable_r
            mfe_time = candle["time"]
        if adverse_r < mae:
            mae = adverse_r
            mae_time = candle["time"]
        if candle.get("spread") is not None:
            spreads.append(float(candle["spread"]))
        closes.append(candle["close"])
        regime = detect_regime(ordered[:index], context=context or {})
        regimes.append(regime | {"previous_regime": previous_regime, "transition": f"{previous_regime}->{regime['regime']}" if previous_regime and previous_regime != regime["regime"] else None})
        previous_regime = regime["regime"]
        timeline.append({"timestamp": candle["time"].isoformat(), "r": close_r, "mfe_r": mfe, "mae_r": mae, "regime": regime["regime"], "available_data_only": True})
    exit_r = _signed_r(case, ordered[-1]["close"], risk_price) if ordered else 0.0
    return sanitize(
        {
            "path_id": "PATH_" + _hash({"trade": case.trade_id, "candles": [row["time"].isoformat() for row in ordered]})[:32],
            "trade_id": case.trade_id,
            "session_id": case.session_id,
            "symbol": case.symbol,
            "direction": case.direction,
            "initial_risk_price": risk_price,
            "initial_monetary_risk": abs(case.actual_pnl / exit_r) if exit_r else 0.0,
            "initial_target_r": target_r,
            "mfe": mfe,
            "mae": mae,
            "max_achieved_r": mfe,
            "min_achieved_r": mae,
            "time_to_mfe_seconds": int((mfe_time - case.entry_time).total_seconds()) if mfe_time else None,
            "time_to_mae_seconds": int((mae_time - case.entry_time).total_seconds()) if mae_time else None,
            "profit_retracement_from_mfe": max(0.0, mfe - exit_r),
            "candle_count_held": len(ordered),
            "spread_at_entry": spreads[0] if spreads else case.spread_at_entry,
            "maximum_spread": max(spreads) if spreads else None,
            "spread_at_exit": spreads[-1] if spreads else None,
            "volatility_at_entry": _realized_vol([row["close"] for row in ordered[:5]]),
            "volatility_during_trade": _realized_vol(closes),
            "market_regime_entry": regimes[0]["regime"] if regimes else "insufficient_data",
            "market_regime_exit": regimes[-1]["regime"] if regimes else "insufficient_data",
            "timeline": timeline,
            "context_snapshot": context or {},
            "raw_payload": {"lookahead_safe": True, "policy": "completed_candle_replay"},
            "regime_snapshots": [
                {
                    "regime_id": "REGIME_" + _hash({"symbol": case.symbol, "timeframe": case.timeframe, "time": row["timestamp"], "version": REGIME_VERSION})[:32],
                    "symbol": case.symbol,
                    "timeframe": case.timeframe,
                    "timestamp": row["timestamp"],
                    "regime": row["regime"],
                    "confidence": 0.7,
                    "features": {"r": row["r"], "mfe_r": row["mfe_r"], "mae_r": row["mae_r"]},
                    "rule_version": REGIME_VERSION,
                    "previous_regime": row.get("previous_regime"),
                    "transition": row.get("transition"),
                }
                for row in timeline
            ],
        }
    )


def detect_regime(candles: list[dict[str, Any]], context: dict[str, Any] | None = None) -> dict[str, Any]:
    if len(candles) < 5:
        return {"regime": "insufficient_data", "confidence": 0.0, "features": {}}
    normalized: list[dict[str, Any]] = []
    for row in candles:
        candle = _normalize_candle(row)
        if candle:
            normalized.append(candle)
    closes = [row["close"] for row in normalized]
    ranges = [row["high"] - row["low"] for row in normalized]
    spread = _avg([float(row.get("spread") or 0) for row in normalized[-5:]])
    volatility = _realized_vol(closes[-12:])
    atr = _avg(ranges[-14:]) if ranges else 0
    slope = closes[-1] - closes[max(0, len(closes) - 6)]
    normal_range = _avg(ranges[:-3]) if len(ranges) > 5 else atr
    event_risk = str((context or {}).get("scheduled_event_risk") or (context or {}).get("headline_risk") or "").lower()
    if "high" in event_risk or "elevated" in event_risk:
        regime = "event_driven"
    elif atr and normal_range and atr > normal_range * 1.8:
        regime = "high_volatility"
    elif atr and normal_range and atr < normal_range * 0.55:
        regime = "low_volatility"
    elif abs(slope) > max(atr, 0.00001) * 2 and slope > 0:
        regime = "trending_up"
    elif abs(slope) > max(atr, 0.00001) * 2 and slope < 0:
        regime = "trending_down"
    elif len(closes) >= 8 and (max(closes[-4:]) > max(closes[:-4]) or min(closes[-4:]) < min(closes[:-4])):
        regime = "breakout"
    elif len(closes) >= 8 and (closes[-1] - closes[-4]) * (closes[-4] - closes[-8]) < 0:
        regime = "reversal"
    elif volatility > max(atr, 0.00001) * 1.5:
        regime = "unstable_transition"
    else:
        regime = "ranging"
    return {"regime": regime, "confidence": 0.7, "features": {"atr": atr, "normalized_atr": atr / closes[-1] if closes[-1] else 0, "realized_volatility": volatility, "ma_slope": slope, "spread": spread}, "rule_version": REGIME_VERSION}


def default_policies() -> list[dict[str, Any]]:
    base = {"version": "v1", "eligible_strategies": [], "eligible_symbols": [], "eligible_timeframes": [], "eligible_regimes": [], "minimum_data_requirements": DEFAULT_MINIMUMS, "validation_status": "research", "scorecard": {}, "evidence_artifacts": []}
    return [
        base | {"policy_id": "static_baseline_v1", "name": "Static baseline", "family": "static", "description": "Original SL/TP, no intervention.", "parameters": {}},
        base | {"policy_id": "partial_25_at_0_5r_v1", "name": "Partial 25 at 0.5R", "family": "partial_profit", "description": "Shadow close 25% at +0.5R.", "parameters": {"trigger_r": 0.5, "fraction": 0.25}},
        base | {"policy_id": "breakeven_at_0_75r_v1", "name": "Break-even at 0.75R", "family": "breakeven", "description": "Shadow move stop to entry after +0.75R.", "parameters": {"trigger_r": 0.75}},
        base | {"policy_id": "trailing_atr_1_5_v1", "name": "ATR trailing 1.5", "family": "trailing", "description": "Shadow trailing exit after adverse move from MFE.", "parameters": {"trigger_r": 1.0, "trail_r": 0.6}},
        base | {"policy_id": "mfe_retrace_50_after_0_75r_v1", "name": "MFE retrace 50 after 0.75R", "family": "mfe_retracement", "description": "Shadow exit when half of achieved MFE is given back.", "parameters": {"min_mfe_r": 0.75, "retrace_fraction": 0.5}},
        base | {"policy_id": "invalidation_momentum_v1", "name": "Momentum invalidation", "family": "thesis_invalidation", "description": "Shadow exit after opposing momentum confirmation.", "parameters": {"opposing_candles": 2}},
        base | {"policy_id": "time_exit_12_candles_v1", "name": "Time exit 12 candles", "family": "time_exit", "description": "Shadow exit if no progress after 12 completed candles.", "parameters": {"max_candles": 12, "minimum_progress_r": 0.25}},
        base | {"policy_id": "context_spread_expansion_v1", "name": "Context spread expansion", "family": "context_exit", "description": "Shadow exit when spread expands abnormally.", "parameters": {"spread_multiple": 2.5}},
        base | {"policy_id": "tp_progress_partial_v1", "name": "TP-progress staged partials", "family": "tp_progress_partial", "description": "Shadow staged partial close at 75%/85% of original TP distance.", "parameters": {"stage1_progress": 0.75, "stage2_progress": 0.85, "stage1_fraction": 0.25, "stage2_fraction": 0.30}},
        base | {"policy_id": "tp_progress_full_protect_95_v1", "name": "TP-progress 95 full protect", "family": "tp_progress_full_protect", "description": "Shadow full close if price gives back below a floor after reaching 95% of original TP distance.", "parameters": {"trigger_progress": 0.95, "floor_fraction": 0.75}},
        base | {"policy_id": "structure_stop_protection_v1", "name": "Structure-based protective stop", "family": "mfe_retracement", "description": "Shadow tighter structure-anchored trailing exit vs the flat MFE-retracement baseline.", "parameters": {"min_mfe_r": 0.5, "retrace_fraction": 0.3}},
        base | {"policy_id": "improved_breakeven_structure_v1", "name": "Stricter structure-aware breakeven", "family": "breakeven", "description": "Shadow breakeven requiring a higher achieved-R trigger than the flat 0.75R baseline, approximating the multi-condition structure gate.", "parameters": {"trigger_r": 1.2}},
        base | {"policy_id": "atr_structure_stop_wider_v1", "name": "Wider ATR/structure stop, risk-normalized volume", "family": "atr_structure_stop", "description": "Shadow counterfactual: initial stop 1.5x wider (risk-normalized volume, same monetary risk), rescaling realized R.", "parameters": {"risk_multiple": 1.5}},
        base | {"policy_id": "atr_structure_stop_tighter_v1", "name": "Tighter ATR/structure stop, risk-normalized volume", "family": "atr_structure_stop", "description": "Shadow counterfactual: initial stop 0.75x tighter (risk-normalized volume, same monetary risk), rescaling realized R.", "parameters": {"risk_multiple": 0.75}},
        base
        | {
            "policy_id": ACTIVE_POLICY_ID,
            "name": "Conservative demo manager",
            "family": "active_demo_manager",
            "description": "Deterministic MT5 demo-only management policy for partial exits, breakeven stops, trailing stops, invalidation exits, event-risk reductions, time exits, TP-progress protection zones, and a volatility-aware profit-lock floor.",
            "parameters": {
                "partial_profit_r": 0.5,
                "partial_profit_fraction": 0.25,
                "breakeven_r": 1.0,
                "breakeven_min_tp_progress": 0.3,
                "mfe_min_r": 0.5,
                "mfe_giveback_allowance": "volatility_and_regime_aware_v2",
                "trail_r": 1.5,
                "trail_distance": "volatility_and_regime_aware_v2",
                "time_exit_candles": 24,
                "tp_progress_zones": {"partial_protect": [0.60, 0.85], "structure_stop": [0.75, 0.95], "profit_lock_floor": [0.80, 0.90]},
                "modification_cooldown_seconds": 120,
            },
            "validation_status": "demo_active_candidate",
        },
    ]


def simulate_policy(case: TradeCase, path: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    timeline = path.get("timeline") or []
    exit_row: dict[str, Any] = timeline[-1] if timeline else {"timestamp": (case.exit_time or case.entry_time).isoformat(), "r": 0}
    action = "HOLD_TO_STATIC_EXIT"
    reason = "baseline_static_sl_tp"
    exit_r = float(exit_row.get("r") or 0)
    family = policy["family"]
    params = policy.get("parameters") or {}
    if family == "partial_profit":
        trigger = float(params.get("trigger_r", 0.5))
        hit = _first_timeline(timeline, lambda row: float(row.get("r") or 0) >= trigger)
        if hit:
            exit_row = hit
            exit_r = trigger * float(params.get("fraction", 0.25)) + float(timeline[-1].get("r") or 0) * (1 - float(params.get("fraction", 0.25)))
            action = "PARTIAL_CLOSE_SHADOW"
            reason = "partial_profit_trigger_reached"
    elif family == "breakeven":
        trigger = float(params.get("trigger_r", 0.75))
        hit = _first_timeline(timeline, lambda row: float(row.get("mfe_r") or 0) >= trigger and float(row.get("r") or 0) <= 0)
        if hit:
            exit_row = hit
            exit_r = 0
            action = "MOVE_STOP_BREAKEVEN_SHADOW"
            reason = "breakeven_trigger_then_retrace"
    elif family == "trailing":
        hit = _first_timeline(timeline, lambda row: float(row.get("mfe_r") or 0) >= float(params.get("trigger_r", 1.0)) and (float(row.get("mfe_r") or 0) - float(row.get("r") or 0)) >= float(params.get("trail_r", 0.6)))
        if hit:
            exit_row = hit
            exit_r = float(hit.get("r") or 0)
            action = "TRAILING_EXIT_SHADOW"
            reason = "trailing_threshold_hit"
    elif family == "mfe_retracement":
        min_mfe = float(params.get("min_mfe_r", 0.75))
        fraction = float(params.get("retrace_fraction", 0.5))
        hit = _first_timeline(timeline, lambda row: float(row.get("mfe_r") or 0) >= min_mfe and float(row.get("r") or 0) <= float(row.get("mfe_r") or 0) * (1 - fraction))
        if hit:
            exit_row = hit
            exit_r = float(hit.get("r") or 0)
            action = "MFE_RETRACEMENT_EXIT_SHADOW"
            reason = "mfe_retracement_threshold_hit"
    elif family == "thesis_invalidation":
        hit = _opposing_momentum(timeline)
        if hit:
            exit_row = hit
            exit_r = float(hit.get("r") or 0)
            action = "INVALIDATION_EXIT_SHADOW"
            reason = "opposing_momentum_confirmation"
    elif family == "time_exit":
        max_candles = int(params.get("max_candles", 12))
        minimum = float(params.get("minimum_progress_r", 0.25))
        if len(timeline) > max_candles and max(float(row.get("r") or 0) for row in timeline[:max_candles]) < minimum:
            exit_row = timeline[max_candles - 1]
            exit_r = float(exit_row.get("r") or 0)
            action = "TIME_EXIT_SHADOW"
            reason = "no_progress_before_time_limit"
    elif family == "context_exit":
        entry_spread = path.get("spread_at_entry") or 0
        if entry_spread and (path.get("maximum_spread") or 0) >= entry_spread * float(params.get("spread_multiple", 2.5)):
            hit = _first_timeline(timeline, lambda row: True)
            if hit:
                exit_row = hit
                exit_r = float(hit.get("r") or 0)
                action = "CONTEXT_EXIT_SHADOW"
                reason = "spread_expansion"
    elif family == "tp_progress_partial":
        target_r = float(path.get("initial_target_r") or 0)
        stage1_r = target_r * float(params.get("stage1_progress", 0.75))
        stage2_r = target_r * float(params.get("stage2_progress", 0.85))
        stage1_fraction = float(params.get("stage1_fraction", 0.25))
        stage2_fraction = float(params.get("stage2_fraction", 0.30))
        final_r = float(timeline[-1].get("r") or 0) if timeline else 0.0
        hit1 = _first_timeline(timeline, lambda row: float(row.get("r") or 0) >= stage1_r) if target_r else None
        hit2 = _first_timeline(timeline, lambda row: float(row.get("r") or 0) >= stage2_r) if target_r else None
        if hit1:
            remaining_fraction = 1 - stage1_fraction - (stage2_fraction if hit2 else 0)
            exit_r = stage1_r * stage1_fraction + (stage2_r * stage2_fraction if hit2 else 0) + final_r * remaining_fraction
            exit_row = hit2 or hit1
            action = "TP_PROGRESS_PARTIAL_SHADOW"
            reason = "staged_partial_at_tp_progress_zones"
    elif family == "tp_progress_full_protect":
        target_r = float(path.get("initial_target_r") or 0)
        trigger_r = target_r * float(params.get("trigger_progress", 0.95))
        floor_fraction = float(params.get("floor_fraction", 0.75))
        hit = _first_timeline(timeline, lambda row: float(row.get("r") or 0) >= trigger_r) if target_r else None
        if hit:
            floor_r = float(hit.get("r") or 0) * floor_fraction
            giveback_hit = _first_timeline(timeline, lambda row: row["timestamp"] > hit["timestamp"] and float(row.get("r") or 0) < floor_r)
            if giveback_hit:
                exit_row = giveback_hit
                exit_r = floor_r
                action = "TP_PROGRESS_FULL_PROTECT_SHADOW"
                reason = "giveback_below_floor_after_95pct_tp_progress"
    elif family == "atr_structure_stop":
        risk_multiple = float(params.get("risk_multiple", 1.0))
        if risk_multiple > 0:
            exit_r = (float(timeline[-1].get("r") or 0) if timeline else exit_r) / risk_multiple
            action = "ATR_STRUCTURE_STOP_RESCALED_SHADOW"
            reason = "initial_stop_rescaled_risk_normalized_volume"
    hypothetical_pnl = _pnl_from_r(case, exit_r)
    actual_pnl = case.actual_pnl
    difference = hypothetical_pnl - actual_pnl
    decision = {"decision_id": "SHADOW_" + _hash({"trade": case.trade_id, "policy": policy["policy_id"], "action": action})[:32], "trade_id": case.trade_id, "policy_id": policy["policy_id"], "timestamp": exit_row["timestamp"], "proposed_action": action, "proposed_volume_fraction": 0 if action == "HOLD_TO_STATIC_EXIT" else 1, "proposed_price": _price_from_r(case, exit_r), "reason": reason, "evidence": {"r": exit_r, "available_data_only": True}, "would_mutate_broker": False}
    return sanitize(
        {
            "outcome_id": "OUTCOME_" + _hash({"trade": case.trade_id, "policy": policy["policy_id"]})[:32],
            "trade_id": case.trade_id,
            "policy_id": policy["policy_id"],
            "actual_pnl": actual_pnl,
            "hypothetical_pnl": hypothetical_pnl,
            "hypothetical_r": exit_r,
            "hypothetical_exit_time": exit_row["timestamp"],
            "hypothetical_exit_price": decision["proposed_price"],
            "difference_from_actual": difference,
            "loss_reduced": actual_pnl < 0 and hypothetical_pnl > actual_pnl,
            "profit_reduced": actual_pnl > 0 and hypothetical_pnl < actual_pnl,
            "tp_later_reached": any(float(row.get("mfe_r") or 0) >= path.get("initial_target_r", 0) for row in timeline),
            "sl_later_reached": any(float(row.get("mae_r") or 0) <= -1 for row in timeline),
            "mfe_capture": exit_r / path["mfe"] if path.get("mfe") else 0,
            "false_early_exit": actual_pnl > hypothetical_pnl and action != "HOLD_TO_STATIC_EXIT",
            "avoided_loss": actual_pnl < 0 and hypothetical_pnl >= 0,
            "giveback_avoided": max(0.0, path.get("profit_retracement_from_mfe", 0) - max(0.0, path.get("mfe", 0) - exit_r)),
            "extra_transaction_costs": 0.0 if action == "HOLD_TO_STATIC_EXIT" else abs(case.commission) * 0.25,
            "applicable": True,
            "first_shadow_decision": decision,
            "raw_payload": {"lookahead_safe": True, "mode": "shadow"},
        }
    )


def reward_definition() -> dict[str, Any]:
    return {"version": REWARD_VERSION, "components": {"realized_r": 1.0, "drawdown_penalty": -0.5, "tail_loss_penalty": -0.75, "transaction_cost_penalty": -0.25, "early_exit_regret": -0.4, "mfe_capture_reward": 0.35, "mae_reduction_reward": 0.25, "stability_penalty": -0.3, "turnover_penalty": -0.2}}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_history_row(row: dict[str, Any], session_id: str, event_type: str) -> dict[str, Any]:
    raw = dict(row)
    utc_time = _parse_dt(raw.get("time") or raw.get("time_done") or raw.get("time_setup"))
    symbol = str(raw.get("symbol") or "UNKNOWN").upper()
    mt5_type = raw.get("type")
    side = _side_from_type(mt5_type)
    order_id = str(raw.get("order") or raw.get("ticket") or "")
    deal_id = str(raw.get("ticket") or "") if event_type == "DEAL" else None
    position_id = str(raw.get("position_id") or raw.get("position") or raw.get("identifier") or order_id or "")
    comment = str(raw.get("comment") or "")
    return {"event_id": "AE_" + _hash({"session": session_id, "type": event_type, "raw": raw})[:40], "session_id": session_id, "trade_id": position_id or order_id or deal_id, "ticket": str(raw.get("ticket") or ""), "order_id": order_id, "deal_id": deal_id, "position_id": position_id, "event_type": event_type, "symbol": symbol, "side": side, "volume": _float(raw.get("volume")), "price": _float(raw.get("price") or raw.get("price_open")), "stop_loss": _float(raw.get("sl")), "take_profit": _float(raw.get("tp")), "commission": _float(raw.get("commission")) or 0.0, "swap": _float(raw.get("swap")) or 0.0, "fee": _float(raw.get("fee")) or 0.0, "realized_pnl": _float(raw.get("profit")) or 0.0, "magic": _int(raw.get("magic")), "comment": comment, "strategy_id": _lineage(comment, "strategy"), "strategy_version": _lineage(comment, "strategy_version"), "setup_id": _lineage(comment, "setup"), "timeframe": _lineage(comment, "timeframe"), "broker_exit_reason": _broker_exit_reason(raw), "server_time": utc_time, "utc_time": utc_time, "actor": _actor(comment), "raw_payload": sanitize(raw)}


def _normalize_position_row(row: dict[str, Any], session_id: str) -> dict[str, Any]:
    raw = dict(row)
    utc_time = _parse_dt(raw.get("time"))
    side = "LONG" if int(raw.get("type") or 0) == 0 else "SHORT"
    ticket = str(raw.get("ticket") or raw.get("identifier") or "")
    return {"event_id": "AE_" + _hash({"session": session_id, "type": "POSITION", "raw": raw})[:40], "session_id": session_id, "trade_id": ticket, "ticket": ticket, "order_id": ticket, "deal_id": None, "position_id": str(raw.get("identifier") or ticket), "event_type": "POSITION", "symbol": str(raw.get("symbol") or "UNKNOWN").upper(), "side": side, "volume": _float(raw.get("volume")), "price": _float(raw.get("price_open")), "stop_loss": _float(raw.get("sl")), "take_profit": _float(raw.get("tp")), "commission": _float(raw.get("commission")) or 0.0, "swap": _float(raw.get("swap")) or 0.0, "fee": 0.0, "realized_pnl": _float(raw.get("profit")) or 0.0, "magic": _int(raw.get("magic")), "comment": raw.get("comment"), "strategy_id": _lineage(raw.get("comment"), "strategy"), "strategy_version": _lineage(raw.get("comment"), "strategy_version"), "setup_id": _lineage(raw.get("comment"), "setup"), "timeframe": _lineage(raw.get("comment"), "timeframe"), "broker_exit_reason": None, "server_time": utc_time, "utc_time": utc_time, "actor": _actor(raw.get("comment")), "raw_payload": sanitize(raw)}


def _event_orm(event: dict[str, Any]) -> AdaptiveTradeEventORM:
    row = AdaptiveTradeEventORM(event_id=event["event_id"])
    _assign(row, event)
    return row


def _regime_orm(payload: dict[str, Any]) -> MarketRegimeSnapshotORM:
    row = MarketRegimeSnapshotORM(regime_id=payload["regime_id"])
    _assign(row, payload)
    return row


def _shadow_decision_orm(payload: dict[str, Any]) -> ShadowDecisionORM:
    row = ShadowDecisionORM(decision_id=payload["decision_id"])
    _assign(row, payload)
    return row


def _outcome_orm(payload: dict[str, Any]) -> CounterfactualOutcomeORM:
    row = CounterfactualOutcomeORM(outcome_id=payload["outcome_id"])
    _assign(row, payload)
    return row


def _assign(row: Any, payload: dict[str, Any]) -> None:
    columns = {column.name for column in row.__table__.columns}
    for key, value in payload.items():
        if key in columns:
            setattr(row, key, _parse_dt(value) if key.endswith("_at") or key in {"timestamp", "hypothetical_exit_time", "started_at", "ended_at", "utc_time", "server_time", "signal_timestamp"} else value)


def _trade_case(trade: dict[str, Any]) -> TradeCase:
    return TradeCase(
        trade_id=str(trade.get("trade_id") or trade.get("position_id") or trade.get("order_ticket") or "UNKNOWN"),
        session_id=trade.get("session_id"),
        symbol=str(trade.get("symbol") or trade.get("broker_symbol") or "UNKNOWN").upper(),
        direction=str(trade.get("direction") or trade.get("side") or "LONG").upper(),
        volume=float(trade.get("volume") or trade.get("lot_size") or 1),
        entry=float(trade.get("entry") or trade.get("entry_price") or trade.get("fill_price") or 0),
        stop_loss=float(trade.get("stop_loss") or trade.get("sl") or 0),
        take_profit=float(trade.get("take_profit") or trade.get("tp") or 0),
        entry_time=_parse_dt(trade.get("entry_time") or trade.get("open_timestamp")) or utcnow(),
        exit_time=_parse_dt(trade.get("exit_time") or trade.get("close_timestamp")),
        actual_pnl=float(trade.get("actual_pnl") or trade.get("realized_pnl") or 0),
        commission=float(trade.get("commission") or 0),
        swap=float(trade.get("swap") or 0),
        spread_at_entry=_float(trade.get("spread_at_entry")),
        strategy_id=str(trade.get("strategy_id") or "UNKNOWN"),
        strategy_version=str(trade.get("strategy_version") or "UNKNOWN"),
        setup_id=str(trade.get("setup_id") or "UNKNOWN"),
        timeframe=str(trade.get("timeframe") or "M5").upper(),
    )


def _normalize_candle(row: dict[str, Any]) -> dict[str, Any] | None:
    ts = _parse_dt(row.get("time") or row.get("timestamp"))
    if not ts:
        return None
    return {"time": ts, "open": float(row.get("open") or 0), "high": float(row.get("high") or 0), "low": float(row.get("low") or 0), "close": float(row.get("close") or 0), "spread": _float(row.get("spread"))}


def _signed_r(case: TradeCase, price: float, risk: float) -> float:
    return (price - case.entry) / risk if case.direction == "LONG" else (case.entry - price) / risk


def _price_from_r(case: TradeCase, r_value: float) -> float:
    risk = abs(case.entry - case.stop_loss) or 0.00001
    return case.entry + risk * r_value if case.direction == "LONG" else case.entry - risk * r_value


def _pnl_from_r(case: TradeCase, r_value: float) -> float:
    risk = abs(case.actual_pnl) if abs(case.actual_pnl) > 0 else 100.0
    return r_value * risk


def _first_timeline(timeline: list[dict[str, Any]], predicate: Any) -> dict[str, Any] | None:
    for row in timeline:
        if predicate(row):
            return row
    return None


def _opposing_momentum(timeline: list[dict[str, Any]]) -> dict[str, Any] | None:
    for idx in range(2, len(timeline)):
        if float(timeline[idx]["r"]) < float(timeline[idx - 1]["r"]) < float(timeline[idx - 2]["r"]):
            return timeline[idx]
    return None


def _relationship_summary(rows: list[AdaptiveTradeEventORM]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: row.utc_time or utcnow())
    relationships: list[dict[str, Any]] = []
    previous_time: datetime | None = None
    previous_price: float | None = None
    for index, row in enumerate(ordered):
        relation = "original_entry" if index == 0 else "independent_new_setup"
        if index > 0 and previous_time and row.utc_time and (row.utc_time - previous_time) <= timedelta(minutes=30):
            relation = "duplicate_signal"
        if index > 0 and previous_price and row.price and abs(row.price - previous_price) / max(abs(previous_price), 0.00001) < 0.001:
            relation = "averaging_or_pyramiding"
        relationships.append({"trade_id": row.trade_id, "relationship": relation, "timestamp": row.utc_time.isoformat() if row.utc_time else None})
        previous_time = row.utc_time
        previous_price = row.price
    return {"relationships": relationships, "repeated_entries": max(0, len(ordered) - 1), "market_regime": "insufficient_data"}


def _correlation_proxy(rows: list[AdaptiveTradeEventORM]) -> float | None:
    if len(rows) < 2:
        return None
    same_side = len({row.side for row in rows}) == 1
    close_times = all(rows[idx].utc_time and rows[idx - 1].utc_time and abs((rows[idx].utc_time - rows[idx - 1].utc_time).total_seconds()) <= 1800 for idx in range(1, len(rows)))
    return 0.9 if same_side and close_times else 0.5 if same_side else 0.0


def _policy_score(policy_id: str | None, rows: list[CounterfactualOutcomeORM]) -> dict[str, Any]:
    if not policy_id:
        return _empty_policy_score(policy_id)
    pnls = [float(row.hypothetical_pnl or 0) for row in rows]
    diffs = [float(row.difference_from_actual or 0) for row in rows]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    sample = len(rows)
    score = _avg(diffs) + (len([row for row in rows if row.loss_reduced]) * 0.5) - (len([row for row in rows if row.false_early_exit]) * 0.75)
    return {"policy_id": policy_id, "sample_size": sample, "score": score, "expectancy": _avg(pnls), "profit_factor": gross_win / gross_loss if gross_loss else None, "losses_reduced": sum(1 for row in rows if row.loss_reduced), "false_early_exits": sum(1 for row in rows if row.false_early_exit), "minimums_passed": _minimums_pass(rows), "promotion_allowed": False}


def _empty_policy_score(policy_id: str | None) -> dict[str, Any]:
    return {"policy_id": policy_id, "sample_size": 0, "score": 0, "expectancy": 0, "profit_factor": None, "losses_reduced": 0, "false_early_exits": 0, "minimums_passed": False, "promotion_allowed": False}


def _group_outcomes(rows: list[CounterfactualOutcomeORM]) -> dict[str, list[CounterfactualOutcomeORM]]:
    grouped: dict[str, list[CounterfactualOutcomeORM]] = {}
    for row in rows:
        grouped.setdefault(row.policy_id, []).append(row)
    return grouped


def _minimums_pass(rows: list[Any]) -> bool:
    return len(rows) >= DEFAULT_MINIMUMS["MIN_OUT_OF_SAMPLE_TRADES"]


def _parameter_stability(scores: dict[str, dict[str, Any]]) -> dict[str, Any]:
    values = [row["score"] for row in scores.values()]
    return {"policy_count": len(values), "score_range": max(values) - min(values) if values else 0, "stable": len(values) >= 2 and (max(values) - min(values)) < 500 if values else False}


def _drift_state(values: list[float]) -> str:
    if len(values) < 10:
        return "insufficient_data"
    midpoint = len(values) // 2
    first = _avg(values[:midpoint])
    second = _avg(values[midpoint:])
    delta = second - first
    if delta < -500:
        return "significant_drift"
    if delta < -200:
        return "degraded"
    if delta < -50:
        return "warning"
    return "stable"


def _effective_config(account: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = mt5_config()
    risk = ai_trading_config()
    return sanitize({"configuration_version": "adaptive_observation_v1", "strategy_version": "active_current_unchanged", "risk_version": "active_current_unchanged", "sizing_version": "active_current_unchanged", "mt5_autonomous_submission_enabled": cfg.autonomous_submission_enabled, "order_submission_enabled": cfg.order_submission_enabled, "live_trading_enabled": cfg.live_trading_enabled, "max_position_size_forex": str(risk.max_position_size_forex), "max_risk_percent": str(risk.max_risk_percent), "max_trade_loss_usd": str(risk.max_trade_loss_usd), "max_open_positions": str(risk.max_open_positions), "account": account or {}})


async def context_for_trade(symbol: str, timestamp: datetime | None = None) -> dict[str, Any]:
    try:
        return await decision_context_service.forex_context(symbol, timestamp)
    except Exception as exc:
        return {"symbol": symbol, "context_unavailable": True, "error": exc.__class__.__name__}


_TIMEFRAME_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


async def _replay_candles(symbol: str, timeframe: str, entry_time: datetime | None, exit_time: datetime | None) -> list[dict[str, Any]]:
    """Fetches enough of the trade's own timeframe to cover its full lifetime (plus lead-in
    context) for reconstruct_path/replay -- unlike _safe_candles (fixed 120-bar window for the
    live management cycle), a closed trade needing replay can span far more bars than that."""
    minutes = _TIMEFRAME_MINUTES.get(timeframe.upper(), 5)
    span_minutes = 120.0
    if entry_time and exit_time:
        span_minutes = max(span_minutes, (_as_aware(exit_time) - _as_aware(entry_time)).total_seconds() / 60.0)
    count = min(500, max(60, int(span_minutes / minutes) + 30))
    try:
        candles = await mt5_adapter.candles(symbol, timeframe.upper(), count=count)
        return [row.model_dump(mode="json") for row in candles]
    except Exception:
        return []


async def _safe_candles(symbol: str) -> list[dict[str, Any]]:
    timeframe = os.getenv("ADAPTIVE_MANAGEMENT_TIMEFRAME", "M5").upper()
    try:
        candles = await mt5_adapter.candles(symbol, timeframe, count=_env_int("ADAPTIVE_MANAGEMENT_CANDLE_COUNT", 120, minimum=20, maximum=500))
        return [row.model_dump(mode="json") for row in candles]
    except Exception as exc:
        return [{"time": utcnow().isoformat(), "open": 0, "high": 0, "low": 0, "close": 0, "quality_flags": [exc.__class__.__name__]}]


def _active_activation(db: Any) -> AdaptiveActivationORM | None:
    return (
        db.query(AdaptiveActivationORM)
        .filter(AdaptiveActivationORM.active.is_(True), AdaptiveActivationORM.mode == "demo_active")
        .order_by(AdaptiveActivationORM.created_at.desc())
        .first()
    )


def _breaker(db: Any) -> AdaptiveCircuitBreakerORM:
    row = db.get(AdaptiveCircuitBreakerORM, "adaptive_demo_manager")
    if row:
        return row
    row = AdaptiveCircuitBreakerORM(breaker_id="adaptive_demo_manager")
    row.state = "closed"
    db.add(row)
    db.flush()
    return row


def _rate_limit_ok(breaker: AdaptiveCircuitBreakerORM, maximum_actions_per_hour: int) -> bool:
    """Enforces activation.maximum_actions_per_hour, which previously existed as a stored
    column that nothing ever read or incremented. Rolling 1-hour window tracked on the
    (already-loaded, already-committed-at-cycle-end) breaker row -- no extra DB round trip."""
    now = utcnow()
    window_start = _as_aware(breaker.actions_hour_window_started_at)
    if not window_start or (now - window_start).total_seconds() >= 3600:
        breaker.actions_hour_window_started_at = now
        breaker.actions_this_hour = 0
    limit = max(1, min(20, int(maximum_actions_per_hour or 6)))
    if breaker.actions_this_hour >= limit:
        return False
    breaker.actions_this_hour += 1
    return True


def _position_id(payload: dict[str, Any]) -> str:
    return str(payload.get("identifier") or payload.get("ticket") or _hash(payload)[:32])


def _position_direction(payload: dict[str, Any]) -> str:
    return "LONG" if int(payload.get("type") or 0) == 0 else "SHORT"


def _position_time(payload: dict[str, Any]) -> datetime | None:
    return _parse_dt(payload.get("time") or payload.get("time_msc"))


def _is_adopted(db: Any, position_id: str) -> bool:
    return bool(db.query(AdaptivePositionAdoptionORM).filter(AdaptivePositionAdoptionORM.position_id == position_id, AdaptivePositionAdoptionORM.active.is_(True)).first())


def _existing_allowed(opened_at: datetime | None, activation: AdaptiveActivationORM | None) -> bool:
    if not activation:
        return False
    if os.getenv("ADAPTIVE_MANAGEMENT_APPLY_TO_EXISTING_POSITIONS", "false").lower() in {"1", "true", "yes", "on"}:
        return True
    effective = _as_aware(activation.effective_from)
    opened = _as_aware(opened_at)
    return bool(opened and effective and opened >= effective)


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _idempotency_key(position_id: str, action_type: str, volume: float | None, sl: float | None, tp: float | None) -> str:
    bucket = utcnow().replace(second=0, microsecond=0).isoformat()
    return "AMI_" + _hash({"position": position_id, "action": action_type, "volume": volume, "sl": sl, "tp": tp, "bucket": bucket})[:48]


def _breakeven_price(state: AdaptivePositionStateORM) -> float:
    entry = float(state.entry_price or 0)
    cost_buffer = _env_float("ADAPTIVE_BREAKEVEN_COST_BUFFER_POINTS", 2.0) * _point_guess(state.symbol)
    return entry + cost_buffer if state.direction == "LONG" else entry - cost_buffer


def _trail_stop(state: AdaptivePositionStateORM, price: float, trail_fraction: float | None = None) -> float | None:
    entry = float(state.entry_price or 0)
    stop = float(state.current_sl or state.original_sl or entry)
    distance = abs(entry - stop)
    if distance <= 0:
        return None
    trail_r = trail_fraction if trail_fraction is not None else _env_float("ADAPTIVE_TRAIL_DISTANCE_R", 0.7)
    return price - distance * trail_r if state.direction == "LONG" else price + distance * trail_r


def _structure_preferred_breakeven(direction: str, cost_adjusted: float, structure_level: float | None, atr: float | None) -> float:
    if structure_level is None or not atr:
        return cost_adjusted
    buffer = atr * 0.1
    structure_stop = structure_level - buffer if direction == "LONG" else structure_level + buffer
    return structure_stop if _stop_improves(direction, structure_stop, cost_adjusted) else cost_adjusted


def _swing_structure_level(normalized_candles: list[dict[str, Any]], direction: str) -> float | None:
    if len(normalized_candles) < 5:
        return None
    lows = [row["low"] for row in normalized_candles]
    highs = [row["high"] for row in normalized_candles]
    return min(lows) if direction == "LONG" else max(highs)


def _resolve_profit_lock_action(winner_classification: str, max_tp_progress: float) -> tuple[str, float]:
    if winner_classification == "invalidated":
        return "FULL", 1.0
    if winner_classification == "critical":
        return ("FULL", 1.0) if max_tp_progress >= 0.95 else ("PARTIAL", 0.7)
    if winner_classification == "weakening":
        return "PARTIAL", 0.5
    if winner_classification == "strong_continuation":
        return "PARTIAL", 0.3
    return "PARTIAL", 0.4


def _stage_already_executed(db: Any, position_id: str, stage: str) -> bool:
    return bool(db.query(AdaptivePartialExitStageORM).filter(AdaptivePartialExitStageORM.position_id == position_id, AdaptivePartialExitStageORM.stage == stage).first())


def _cooldown_elapsed(last_management_at: datetime | None) -> bool:
    if not last_management_at:
        return True
    cooldown = _env_int("ADAPTIVE_MODIFICATION_COOLDOWN_SECONDS", 120, minimum=10, maximum=3600)
    last = _as_aware(last_management_at)
    return bool(last and (utcnow() - last).total_seconds() >= cooldown)


def _price_matches(requested: float | None, actual: float | None, tolerance_fraction: float = 0.0005) -> bool:
    if requested is None or actual is None:
        return requested == actual
    tolerance = max(abs(requested) * tolerance_fraction, 1e-6)
    return abs(requested - actual) <= tolerance


def _stop_improves(direction: str, proposed: float | None, current: float | None) -> bool:
    if proposed is None:
        return False
    if current is None or current == 0:
        return True
    return proposed > current if direction == "LONG" else proposed < current


def _opposing_candles(candles: list[dict[str, Any]], direction: str) -> int:
    count = 0
    for row in reversed([_normalize_candle(c) for c in candles][-5:]):
        if not row:
            continue
        bearish = row["close"] < row["open"]
        bullish = row["close"] > row["open"]
        if (direction == "LONG" and bearish) or (direction == "SHORT" and bullish):
            count += 1
        else:
            break
    return count


def _candles_held(opened_at: datetime | None, candles: list[dict[str, Any]]) -> int:
    if not opened_at:
        return 0
    return sum(1 for row in candles if (_normalize_candle(row) or {}).get("time") and (_normalize_candle(row) or {})["time"] >= opened_at)


def _normalize_volume(volume: float, symbol: Any) -> float:
    step = Decimal(str(symbol.volume_step or "0.01"))
    minimum = Decimal(str(symbol.volume_min or "0.01"))
    maximum = Decimal(str(symbol.volume_max or "100"))
    value = Decimal(str(max(0.0, volume)))
    rounded = (value / step).to_integral_value(rounding=ROUND_FLOOR) * step
    bounded = min(max(rounded, Decimal("0")), maximum)
    return float(bounded if bounded >= minimum else Decimal("0"))


def _round_price(price: float | None, symbol: Any) -> float:
    if price is None:
        return 0.0
    return round(float(price), int(symbol.digits or 5))


def _point_guess(symbol: str) -> float:
    return 0.01 if symbol.upper().endswith("JPY") else 0.0001


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
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


def _env_csv(name: str) -> list[str]:
    value = os.getenv(name, "")
    return [part.strip().upper() for part in value.split(",") if part.strip()]


def _asdict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "_asdict"):
        return value._asdict()
    if isinstance(value, dict):
        return value
    try:
        return dict(value)
    except Exception:
        return {"value": str(value)}


def _orm_dict(row: Any) -> dict[str, Any]:
    return {column.name: sanitize(getattr(row, column.name)) for column in row.__table__.columns}


def _side_from_type(value: Any) -> str:
    try:
        return "LONG" if int(value) in {0, 2, 4, 6} else "SHORT"
    except Exception:
        return "UNKNOWN"


def _is_trade_symbol(value: Any) -> bool:
    symbol = str(value or "").upper()
    return bool(symbol and symbol != "UNKNOWN")


def _lineage(comment: Any, key: str) -> str:
    text = str(comment or "")
    marker = f"{key}="
    if marker in text:
        return text.split(marker, 1)[1].split()[0].strip(",;")
    bsm = _parse_bsm_comment(text, key)
    if bsm is not None:
        return bsm
    if "BENSIM_AUTO" in text:
        return {"strategy": "BENSIM_AUTO", "strategy_version": "UNKNOWN", "setup": "UNKNOWN", "timeframe": "UNKNOWN"}[key]
    return "UNKNOWN"


def _parse_bsm_comment(text: str, key: str) -> str | None:
    if not text.startswith("BSM|"):
        return None
    parts = text.split("|")
    if len(parts) < 3:
        return None
    mapping = {"strategy": parts[1], "strategy_version": "v1", "timeframe": parts[2], "setup": "MT5_AUTONOMOUS_ENTRY"}
    return mapping.get(key)


def _bsm_comment(strategy_id: str | None, timeframe: str | None, tag: str) -> str:
    code = (strategy_id or "UNK")[:8]
    tf = (timeframe or "NA")[:4]
    return f"BSM|{code}|{tf}|{tag}"[:31]


def _actor(comment: Any) -> str:
    text = str(comment or "").upper()
    if "BENSIM" in text or "AUTO" in text:
        return "SYSTEM"
    if "MANUAL" in text:
        return "MANUAL"
    return "UNKNOWN"


def _broker_exit_reason(row: dict[str, Any]) -> str | None:
    comment = str(row.get("comment") or "").lower()
    if "tp" in comment:
        return "TAKE_PROFIT"
    if "sl" in comment or "stop" in comment:
        return "STOP_LOSS"
    return None


def _realized_vol(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    returns = [(values[idx] - values[idx - 1]) / values[idx - 1] for idx in range(1, len(values)) if values[idx - 1]]
    if not returns:
        return 0.0
    avg = sum(returns) / len(returns)
    return (sum((row - avg) ** 2 for row in returns) / len(returns)) ** 0.5


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _session_label(opened_at: datetime | None) -> str:
    aware = _as_aware(opened_at)
    if not aware:
        return "UNKNOWN"
    hour = aware.astimezone(timezone.utc).hour
    if 7 <= hour < 12:
        return "LONDON"
    if 12 <= hour < 16:
        return "LONDON_NEW_YORK_OVERLAP"
    if 16 <= hour < 21:
        return "NEW_YORK"
    return "ASIA"


def _volatility_state_label(stop_audit: Any) -> str:
    multiple = getattr(stop_audit, "sl_atr_multiple", None) if stop_audit else None
    if multiple is None:
        return "insufficient_data"
    if multiple < 1.5:
        return "low_relative_volatility"
    if multiple > 2.5:
        return "high_relative_volatility"
    return "normal_relative_volatility"


def _audit_group_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    if count < DEFAULT_MINIMUMS["MIN_TRADES_PER_STRATEGY_POLICY"]:
        return {"sample_size": count, "insufficient_data": True}
    reached_80_95 = [row for row in rows if row.get("max_tp_progress") and 0.80 <= row["max_tp_progress"] < 0.95]
    return {
        "sample_size": count,
        "insufficient_data": False,
        "trades_reaching_80_95_tp_progress": len(reached_80_95),
        "management_cut_future_winner_count": sum(1 for row in rows if row.get("management_cut_future_winner")),
        "management_failed_to_protect_large_profit_count": sum(1 for row in rows if row.get("management_failed_to_protect_large_profit")),
        "stop_too_tight_count": sum(1 for row in rows if row.get("stop_quality_classification") == "stop_too_tight"),
        "avg_profit_retracement_after_mfe_r": _avg([float(row.get("profit_retracement_after_mfe_r") or 0) for row in rows]),
        "avg_realized_pnl": _avg([float(row.get("realized_pnl") or 0) for row in rows]),
        "stop_outs_count": sum(1 for row in rows if row.get("exit_reason") == "STOP_LOSS" or (row.get("realized_pnl") or 0) < 0),
    }


def _post_trade_aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Portfolio-wide post-trade analytics -- expectancy/profit-factor/MFE-capture/giveback,
    plus break-even, partial-profit and trailing outcome buckets, so aggregate expectancy
    (not individual trade screenshots) can be optimized per the task's explicit instruction."""
    count = len(records)
    if count == 0:
        return {"sample_size": 0, "insufficient_data": True}

    def _bucket(predicate: Any) -> dict[str, Any]:
        rows = [row for row in records if predicate(row)]
        pnls = [float(row.get("realized_pnl") or 0) for row in rows]
        return {
            "count": len(rows),
            "avg_realized_pnl": _avg(pnls) if pnls else None,
            "win_rate": (sum(1 for p in pnls if p > 0) / len(pnls)) if pnls else None,
        }

    pnls = [float(row.get("realized_pnl") or 0) for row in records]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    mfe_r_values = [float(row.get("mfe_r") or 0) for row in records]
    exit_r_values = [float(row["exit_r"]) for row in records if row.get("exit_r") is not None]
    capture_ratios = [float(row["mfe_capture_ratio"]) for row in records if row.get("mfe_capture_ratio") is not None]
    sl_atr_values = [float(row["sl_atr_multiple"]) for row in records if row.get("sl_atr_multiple") is not None]
    reached_0_5r = [row for row in records if row.get("reached_0_5r")]
    reached_1r = [row for row in records if row.get("reached_1r")]

    return {
        "sample_size": count,
        "insufficient_data": count < DEFAULT_MINIMUMS["MIN_TRADES_PER_POLICY_GLOBAL"],
        "expectancy_usd": _avg(pnls),
        "profit_factor": (sum(wins) / abs(sum(losses))) if losses else (float("inf") if wins else None),
        "win_rate": (len(wins) / count) if count else None,
        "avg_mfe_r": _avg(mfe_r_values),
        "avg_exit_r": _avg(exit_r_values) if exit_r_values else None,
        "avg_mfe_capture_ratio": _avg(capture_ratios) if capture_ratios else None,
        "avg_sl_distance_atr_multiple": _avg(sl_atr_values) if sl_atr_values else None,
        "trades_reaching_0_5r_count": len(reached_0_5r),
        "trades_reaching_0_5r_then_losing_count": sum(1 for row in reached_0_5r if float(row.get("realized_pnl") or 0) <= 0),
        "trades_reaching_1r_count": len(reached_1r),
        "trades_reaching_1r_then_losing_count": sum(1 for row in reached_1r if float(row.get("realized_pnl") or 0) <= 0),
        "giveback_over_50pct_count": sum(1 for row in records if row.get("giveback_over_50pct")),
        "ever_profitable_count": sum(1 for row in records if row.get("ever_profitable")),
        "ever_profitable_then_closed_at_loss_count": sum(1 for row in records if row.get("ever_profitable") and float(row.get("realized_pnl") or 0) <= 0),
        "breakeven_results": _bucket(lambda row: row.get("breakeven_action_taken")),
        "partial_profit_results": _bucket(lambda row: row.get("partial_profit_action_taken")),
        "trailing_results": _bucket(lambda row: row.get("trailing_action_taken")),
        "no_management_action_results": _bucket(lambda row: not (row.get("breakeven_action_taken") or row.get("partial_profit_action_taken") or row.get("trailing_action_taken"))),
    }


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def _hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(sanitize(payload), sort_keys=True, default=str).encode("utf-8")).hexdigest()


adaptive_management_service = AdaptiveManagementService()
