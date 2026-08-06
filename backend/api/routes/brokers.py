from __future__ import annotations

import importlib.util
import os
import socket
import uuid
from decimal import Decimal, ROUND_FLOOR
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.auth.deps import get_current_user
from backend.brokers import broker_registry
from backend.brokers.errors import BrokerError
from backend.brokers.mt5.autonomous import mt5_autonomous_service
from backend.brokers.mt5.persistence import (
    history_availability,
    learning_context_for_candidate,
    query_candidates,
    query_cycles,
    query_decisions,
    query_trade_memory,
    query_trade_reviews,
    query_trades,
    refresh_trade_memory,
    retention_policies,
)
from backend.brokers.ibkr.configuration import ibkr_config
from backend.brokers.models import BrokerCancelCommand, BrokerOrderCommand
from backend.forex_strategies.ibkr_acceptance import ibkr_acceptance_service
from backend.intelligence.trading.config import ai_trading_config
from backend.models.user import User

router = APIRouter(prefix="/api/brokers", tags=["brokers"])


class HistoricalRequest(BaseModel):
    instrument_id: str
    bar_size: str = "15 mins"
    duration: str = "1 D"


class FlattenFxRequest(BaseModel):
    manual_confirmation: bool = False
    reason: str = "user requested flatten"


class CancelBrokerOrderRequest(BaseModel):
    manual_confirmation: bool = False
    reason: str = "user requested broker cancel"


class FxFundingCheckRequest(BaseModel):
    instrument_id: str
    side: str
    quantity: Decimal
    price: Decimal | None = None
    order_type: str = "MARKET"


class FxRearmReadinessRequest(BaseModel):
    symbol: str = "EURUSD"
    representative_quantity: Decimal = Decimal("1000")
    max_quantity: Decimal | None = None
    entry_price: Decimal | None = None
    long_stop_price: Decimal | None = None
    long_take_profit: Decimal | None = None
    short_stop_price: Decimal | None = None
    short_take_profit: Decimal | None = None


class MT5CandlesRequest(BaseModel):
    symbol: str
    timeframe: str = "M15"
    count: int = 100


@router.get("")
async def brokers(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": [{"broker": adapter.name, "capabilities": adapter.capabilities.model_dump(mode="json")} for adapter in broker_registry.list()]}


@router.get("/{broker}/health")
async def broker_health(broker: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return (await _adapter(broker).health()).model_dump(mode="json")


@router.get("/{broker}/capabilities")
async def broker_capabilities(broker: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return _adapter(broker).capabilities.model_dump(mode="json")


@router.post("/{broker}/connect")
async def broker_connect(broker: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    try:
        if broker.lower() == "ibkr":
            return (await ibkr_acceptance_service.connect_and_verify()).model_dump(mode="json")
        return (await _adapter(broker).connect()).model_dump(mode="json")
    except BrokerError as exc:
        raise _safe(exc) from exc


@router.post("/{broker}/disconnect")
async def broker_disconnect(broker: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return (await _adapter(broker).disconnect()).model_dump(mode="json")
    except BrokerError as exc:
        raise _safe(exc) from exc


@router.post("/{broker}/reconnect")
async def broker_reconnect(broker: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return (await _adapter(broker).reconnect()).model_dump(mode="json")
    except BrokerError as exc:
        raise _safe(exc) from exc


@router.post("/mt5/connect")
async def mt5_connect(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return (await _adapter("mt5").connect()).model_dump(mode="json")


@router.post("/mt5/shutdown")
async def mt5_shutdown(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    await _adapter("mt5").shutdown()
    return {"status": "SHUTDOWN", "order_submission_status": "READ_ONLY"}


@router.get("/mt5/status")
async def mt5_status(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    adapter = _adapter("mt5")
    return {
        "health": (await adapter.health()).model_dump(mode="json"),
        "terminal": (await adapter.terminal_status()).model_dump(mode="json"),
        "capabilities": adapter.capabilities.model_dump(mode="json"),
    }


@router.get("/mt5/account")
async def mt5_account(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return (await _adapter("mt5").mt5_account()).model_dump(mode="json")


@router.get("/mt5/terminal")
async def mt5_terminal(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    adapter = _adapter("mt5")
    return {"terminal": (await adapter.terminal_status()).model_dump(mode="json"), "bridge": adapter.bridge.status().__dict__}


@router.get("/mt5/positions")
async def mt5_positions(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": [row.model_dump(mode="json") for row in await _adapter("mt5").mt5_positions()]}


@router.get("/mt5/orders")
async def mt5_orders(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": [row.model_dump(mode="json") for row in await _adapter("mt5").mt5_orders()]}


@router.get("/mt5/history")
async def mt5_history(days: int = 30, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    history = await _adapter("mt5").history(days=days)
    return {key: [row.model_dump(mode="json") for row in rows] for key, rows in history.items()}


@router.get("/mt5/history/orders")
async def mt5_history_orders(days: int = 30, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": [row.model_dump(mode="json") for row in await _adapter("mt5").history_orders(days=days)]}


@router.get("/mt5/history/deals")
async def mt5_history_deals(days: int = 30, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": [row.model_dump(mode="json") for row in await _adapter("mt5").history_deals(days=days)]}


@router.get("/mt5/symbols")
async def mt5_symbols(group: str | None = None, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": [row.model_dump(mode="json") for row in await _adapter("mt5").symbols(group=group)]}


@router.get("/mt5/symbols/{symbol}")
async def mt5_symbol_info(symbol: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    adapter = _adapter("mt5")
    selected = await adapter.symbol_select(symbol, True)
    return {"selected": selected, "symbol": (await adapter.symbol_info(symbol)).model_dump(mode="json")}


@router.get("/mt5/quotes")
async def mt5_quotes(symbols: str | None = Query(default=None), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    requested = [part.strip() for part in (symbols or "").split(",") if part.strip()] or None
    return {"items": [row.model_dump(mode="json") for row in await _adapter("mt5").quotes(requested)]}


@router.get("/mt5/quotes/{symbol}")
async def mt5_quote(symbol: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return (await _adapter("mt5").latest_tick(symbol)).model_dump(mode="json")


@router.get("/mt5/candles/{symbol}")
async def mt5_candles_get(symbol: str, timeframe: str = "M15", count: int = 100, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    count = max(1, min(5000, int(count)))
    return {"items": [row.model_dump(mode="json") for row in await _adapter("mt5").candles(symbol, timeframe, count=count)]}


@router.post("/mt5/candles")
async def mt5_candles(payload: MT5CandlesRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    count = max(1, min(5000, int(payload.count)))
    return {"items": [row.model_dump(mode="json") for row in await _adapter("mt5").candles(payload.symbol, payload.timeframe, count=count)]}


@router.get("/mt5/forex-universe")
async def mt5_forex_universe(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return (await _adapter("mt5").forex_universe()).model_dump(mode="json")


@router.get("/mt5/scheduler/status")
async def mt5_scheduler_status(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return (await _adapter("mt5").scheduler_status()).model_dump(mode="json")


@router.get("/mt5/ai-usage")
async def mt5_ai_usage(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await _adapter("mt5").ai_usage_controls()


@router.get("/mt5/autonomous/status")
async def mt5_autonomous_status(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return mt5_autonomous_service.status()


@router.post("/mt5/autonomous/dry-run")
async def mt5_autonomous_dry_run(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Runs one full autonomous cycle (screening, decision context, economic guard, portfolio/
    risk sizing, SL/TP construction) but never calls order_send -- dry_run is hardcoded True and
    is not a request parameter, so this route can never be used to submit a real order."""
    return await mt5_autonomous_service.run_cycle(owner="api", dry_run=True)


@router.get("/mt5/autonomous/cycles")
async def mt5_autonomous_cycles(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": mt5_autonomous_service.cycles()}


@router.get("/mt5/autonomous/candidates")
async def mt5_autonomous_candidates(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": mt5_autonomous_service.candidates()}


@router.get("/mt5/autonomous/decisions")
async def mt5_autonomous_decisions(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": mt5_autonomous_service.decisions()}


@router.get("/mt5/autonomous/trades")
async def mt5_autonomous_trades(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": mt5_autonomous_service.trades()}


@router.get("/mt5/persistence/cycles")
async def mt5_persisted_cycles(limit: int = 100, symbol: str | None = None, status: str | None = None, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": query_cycles(limit=max(1, min(1000, limit)), symbol=symbol, status=status)}


@router.get("/mt5/persistence/candidates")
async def mt5_persisted_candidates(limit: int = 100, symbol: str | None = None, rejected: bool | None = None, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": query_candidates(limit=max(1, min(1000, limit)), symbol=symbol, rejected=rejected)}


@router.get("/mt5/persistence/decisions")
async def mt5_persisted_decisions(limit: int = 100, symbol: str | None = None, decision: str | None = None, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": query_decisions(limit=max(1, min(1000, limit)), symbol=symbol, decision=decision)}


@router.get("/mt5/persistence/trades")
async def mt5_persisted_trades(limit: int = 100, symbol: str | None = None, session: str | None = None, regime: str | None = None, exit_reason: str | None = None, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": query_trades(limit=max(1, min(1000, limit)), symbol=symbol, session=session, regime=regime, exit_reason=exit_reason)}


@router.get("/mt5/persistence/trade-reviews")
async def mt5_persisted_trade_reviews(limit: int = 100, outcome: str | None = None, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": query_trade_reviews(limit=max(1, min(1000, limit)), outcome=outcome)}


@router.get("/mt5/persistence/trade-memory")
async def mt5_persisted_trade_memory(limit: int = 100, symbol: str | None = None, recommendation: str | None = None, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": query_trade_memory(limit=max(1, min(1000, limit)), symbol=symbol, recommendation=recommendation)}


@router.post("/mt5/persistence/trade-memory/refresh")
async def mt5_trade_memory_refresh(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return refresh_trade_memory()


@router.get("/mt5/persistence/trade-memory/context")
async def mt5_trade_memory_context(symbol: str, session: str | None = None, regime: str | None = None, direction: str | None = None, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return learning_context_for_candidate({"canonical_pair": symbol, "session": session, "market_regime": regime, "direction": direction})


@router.get("/mt5/persistence/history-availability")
async def mt5_history_availability(dataset_policy: str | None = "MT5_ONLY", current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {
        "policy": {
            "live_autonomous_trading": "MT5_ONLY",
            "mt5": "authoritative for live decisions and recent broker-specific analysis",
            "yahoo": "research/backfill only when explicitly selected",
            "canonical_backfill": "versioned dataset snapshots only after overlap, gap, adjustment and lineage validation",
        },
        "items": history_availability(provider="MT5", dataset_policy=dataset_policy),
    }


@router.get("/mt5/persistence/retention")
async def mt5_retention(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": retention_policies()}


@router.get("/mt5/reconciliation")
async def mt5_reconciliation(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await mt5_autonomous_service.reconciliation()


@router.get("/mt5/risk/status")
async def mt5_risk_status(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await mt5_autonomous_service.risk_status()


@router.get("/mt5/performance")
async def mt5_performance(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await mt5_autonomous_service.performance()


@router.post("/mt5/reconciliation/run")
async def mt5_reconciliation_run(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await mt5_autonomous_service.reconciliation()


@router.post("/mt5/emergency-disable")
async def mt5_emergency_disable(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await mt5_autonomous_service.emergency_disable()


@router.post("/mt5/emergency-enable")
async def mt5_emergency_enable(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await mt5_autonomous_service.emergency_enable()


@router.get("/mt5/verification")
async def mt5_verification(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    adapter = _adapter("mt5")
    health = await adapter.connect()
    terminal = await adapter.terminal_status()
    account = await adapter.mt5_account()
    symbols = await adapter.verify_symbols()
    universe = await adapter.forex_universe()
    candles: dict[str, Any] = {}
    for timeframe in ("M15", "H1", "H4"):
        rows = await adapter.candles("EURUSD", timeframe, count=100)
        candles[timeframe] = {"count": len(rows), "first": rows[0].model_dump(mode="json") if rows else None, "last": rows[-1].model_dump(mode="json") if rows else None}
    return {
        "connection_status": health.model_dump(mode="json"),
        "terminal": terminal.model_dump(mode="json"),
        "account": account.model_dump(mode="json"),
        "symbols": symbols,
        "forex_universe": universe.model_dump(mode="json"),
        "candles": candles,
        "order_submission_status": "READ_ONLY",
        "place_order_calls": 0,
        "openai_calls": 0,
    }


@router.get("/mt5/preflight")
async def mt5_preflight(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Read-only composite health check -- connects, reads account/terminal/symbol state, and
    checks account-identity/live-trading/prop-trading gates plus Portfolio/Execution/Adaptive
    Manager and economic-intelligence provider health, without submitting any order. Returns
    READY/DEGRADED/BLOCKED/UNAVAILABLE with the exact reasons behind the verdict."""
    from backend.adaptive_management.service import adaptive_management_service
    from backend.brokers.mt5 import account_registry
    from backend.brokers.mt5.config import mt5_config
    from backend.economic_intelligence.service import economic_intelligence_service
    from backend.portfolio_execution.service import execution_manager, portfolio_manager

    adapter = _adapter("mt5")
    try:
        health = await adapter.connect()
        terminal = await adapter.terminal_status()
        account = await adapter.mt5_account()
    except Exception as exc:
        return {"status": "UNAVAILABLE", "blockers": [f"MT5_CONNECTION_FAILED:{exc.__class__.__name__}"], "degraded_reasons": []}

    cfg = mt5_config()
    fingerprint = account_registry.fingerprint_account(account)
    profile = account_registry.get_profile(fingerprint.fingerprint_hash)
    account_blockers = account_registry.account_blockers(account, account_mode=cfg.account_mode)

    blockers: list[str] = list(account_blockers)
    degraded: list[str] = []
    if not terminal.connected or not terminal.initialize_success:
        blockers.append("TERMINAL_NOT_CONNECTED")
    elif health.state.value not in {"HEALTHY", "DEGRADED"}:
        blockers.append(f"BROKER_HEALTH_{health.state.value}")
    if terminal.account_mode != "DEMO" or cfg.account_mode != "DEMO":
        blockers.append("ACCOUNT_TRADE_MODE_NOT_DEMO")
    if cfg.live_trading_enabled:
        blockers.append("LIVE_TRADING_ENABLED_UNEXPECTED")

    symbols: list[str] = []
    try:
        symbols = await adapter.verify_symbols()
        configured = [s.strip().upper() for s in (os.getenv("MT5_TRADING_SYMBOLS") or "").split(",") if s.strip()]
        missing_symbols = [s for s in configured if s not in symbols]
        if missing_symbols:
            degraded.append(f"SYMBOLS_UNAVAILABLE:{','.join(missing_symbols)}")
    except Exception as exc:
        degraded.append(f"SYMBOL_VERIFICATION_FAILED:{exc.__class__.__name__}")

    fresh_tick = False
    try:
        tick = await adapter.latest_tick("EURUSD")
        fresh_tick = bool(tick.bid and tick.ask)
        if not fresh_tick:
            degraded.append("NO_FRESH_TICK_EURUSD")
    except Exception as exc:
        degraded.append(f"TICK_UNAVAILABLE:{exc.__class__.__name__}")

    if not (terminal.trade_allowed and terminal.ea_trading_allowed):
        degraded.append("TERMINAL_AUTOTRADING_DISABLED")

    autonomous_status = mt5_autonomous_service.status()
    if autonomous_status.get("emergency_disable"):
        blockers.append("MT5_EMERGENCY_DISABLED")

    try:
        portfolio_manager.status()
        portfolio_healthy = True
    except Exception as exc:
        degraded.append(f"PORTFOLIO_MANAGER_UNHEALTHY:{exc.__class__.__name__}")
        portfolio_healthy = False
    try:
        execution_status = execution_manager.status()
        execution_healthy = True
    except Exception as exc:
        degraded.append(f"EXECUTION_MANAGER_UNHEALTHY:{exc.__class__.__name__}")
        execution_healthy = False
        execution_status = None
    try:
        adaptive_status = adaptive_management_service.status()
        adaptive_healthy = True
    except Exception as exc:
        degraded.append(f"ADAPTIVE_MANAGER_UNHEALTHY:{exc.__class__.__name__}")
        adaptive_healthy = False
        adaptive_status = None
    try:
        provider_health = await economic_intelligence_service.provider_health_report()
        if str(provider_health.get("status") or "").upper() not in {"OK", "HEALTHY", ""}:
            degraded.append(f"ECONOMIC_INTELLIGENCE_PROVIDER_DEGRADED:{provider_health.get('status')}")
    except Exception as exc:
        degraded.append(f"ECONOMIC_INTELLIGENCE_PROVIDER_UNAVAILABLE:{exc.__class__.__name__}")
        provider_health = None

    status = "BLOCKED" if blockers else ("DEGRADED" if degraded else "READY")
    return {
        "status": status,
        "blockers": blockers,
        "degraded_reasons": degraded,
        "connection_status": health.model_dump(mode="json"),
        "terminal": terminal.model_dump(mode="json"),
        "account": account.model_dump(mode="json"),
        "account_fingerprint": fingerprint.fingerprint_hash,
        "account_profile": profile,
        "symbols_available": symbols,
        "fresh_tick_available": fresh_tick,
        "emergency_disable": bool(autonomous_status.get("emergency_disable")),
        "portfolio_manager_healthy": portfolio_healthy,
        "execution_manager_healthy": execution_healthy,
        "execution_manager_status": execution_status,
        "adaptive_manager_healthy": adaptive_healthy,
        "adaptive_manager_status": adaptive_status,
        "economic_intelligence_provider_health": provider_health,
        "order_submission_status": "READ_ONLY",
        "place_order_calls": 0,
    }


@router.get("/ibkr/status")
async def ibkr_status(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await ibkr_acceptance_service.status()


@router.get("/ibkr/environment-check")
async def ibkr_environment_check(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    status = await ibkr_acceptance_service.status()
    adapter = broker_registry.get("ibkr")
    real_client = getattr(adapter, "real_client", None)
    manager = getattr(adapter, "session_manager", None)
    readiness = manager.diagnostics() if manager and hasattr(manager, "diagnostics") else real_client.readiness_diagnostics() if real_client and hasattr(real_client, "readiness_diagnostics") else {}
    tcp_reachable = _tcp_reachable(ibkr_config.host, ibkr_config.port, timeout_seconds=1.5)
    blocking_reasons: list[str] = []
    if not ibkr_config.enabled:
        blocking_reasons.append("IBKR_DISABLED")
    if ibkr_config.mode == "PAPER" and importlib.util.find_spec("ibapi") is None:
        blocking_reasons.append("REAL_IBKR_LIBRARY_UNAVAILABLE")
    if ibkr_config.mode == "PAPER" and getattr(adapter.config, "simulated", True):
        blocking_reasons.append("REAL_IBKR_PAPER_REQUIRES_NON_SIMULATED_ADAPTER")
    if ibkr_config.mode == "PAPER" and not ibkr_config.expected_account:
        blocking_reasons.append("EXPECTED_ACCOUNT_MISSING")
    if ibkr_config.mode == "PAPER" and not ibkr_config.account_allow_list:
        blocking_reasons.append("ACCOUNT_ALLOWLIST_MISSING")
    if ibkr_config.mode == "PAPER" and not tcp_reachable:
        blocking_reasons.append("TWS_OR_GATEWAY_PORT_UNREACHABLE")
    if not status.get("account_verified"):
        blocking_reasons.append("ACCOUNT_NOT_VERIFIED")
    last_reconciliation = status.get("last_reconciliation") or {}
    scope_metadata = last_reconciliation.get("scope_metadata") or {}

    return {
        "ibapi_available": importlib.util.find_spec("ibapi") is not None,
        "adapter_mode": ibkr_config.adapter_mode,
        "configured_host": ibkr_config.host,
        "configured_port": ibkr_config.port,
        "tcp_reachable": tcp_reachable,
        "socket_connected": bool(readiness.get("socket_connected", False)),
        "event_loop_thread_alive": bool(readiness.get("event_loop_thread_alive", False)),
        "connect_ack_received": bool(readiness.get("connect_ack_received", False)),
        "next_valid_id_received": bool(readiness.get("next_valid_id_received", False)),
        "managed_accounts_received": bool(readiness.get("managed_accounts_received", False)),
        "current_time_received": bool(readiness.get("current_time_received", False)),
        "api_ready": bool(readiness.get("api_ready", False)),
        "client_id": readiness.get("client_id", ibkr_config.client_id),
        "connection_attempt_id": readiness.get("connection_attempt_id"),
        "last_disconnect_reason": readiness.get("last_disconnect_reason"),
        "readiness_timestamps": readiness.get("timestamps", {}),
        "api_handshake_ready": bool(real_client and getattr(real_client, "api_ready", False)),
        "active_request_count": int(readiness.get("pending_requests", getattr(real_client, "active_request_count", 0) if real_client else 0) or 0),
        "last_ib_error_code": readiness.get("last_error_code") or ((getattr(real_client, "last_error", None) or {}).get("code") if real_client else None),
        "broker_connected": status.get("connection_state") in {"CONNECTED", "ACCOUNT_VERIFIED"},
        "account_verification_state": "VERIFIED" if status.get("account_verified") else "UNVERIFIED",
        "market_data_mode": ibkr_config.market_data_mode,
        "reconciliation_scope": {
            "active_source": scope_metadata.get("active_source"),
            "included_local_record_count": scope_metadata.get("included_local_record_count"),
            "excluded_fixture_count": scope_metadata.get("excluded_fixture_count"),
            "excluded_quarantined_count": scope_metadata.get("excluded_quarantined_count"),
            "excluded_other_account_count": scope_metadata.get("excluded_other_account_count"),
            "excluded_other_environment_count": scope_metadata.get("excluded_other_environment_count"),
            "last_status": last_reconciliation.get("status"),
            "last_clean_reconciliation_timestamp": last_reconciliation.get("created_at") if last_reconciliation.get("status") in {"MATCHED", "MATCHED_EMPTY"} else None,
        },
        "recovery_state": status.get("recovery", {}),
        "blocking_reasons": blocking_reasons,
    }


@router.get("/ibkr/account")
async def ibkr_account(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return (await ibkr_acceptance_service.connect_and_verify()).model_dump(mode="json")


@router.get("/ibkr/contracts")
async def ibkr_contracts(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": [row.model_dump(mode="json") for row in await ibkr_acceptance_service.contracts()]}


@router.post("/ibkr/connect")
async def ibkr_acceptance_connect(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return (await ibkr_acceptance_service.connect_and_verify()).model_dump(mode="json")


@router.post("/ibkr/disconnect")
async def ibkr_acceptance_disconnect(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await ibkr_acceptance_service.disconnect()


@router.post("/ibkr/reconcile")
async def ibkr_acceptance_reconcile(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return ibkr_acceptance_service.reconcile().model_dump(mode="json")


@router.post("/ibkr/contracts/resolve")
async def resolve_ibkr_contract(payload: dict[str, Any], current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    try:
        symbol = str(payload.get("symbol") or payload.get("instrument_id") or "").replace("FX:", "").replace("/", "").upper()
        if symbol in {"EURUSD", "GBPUSD", "USDJPY", "XAUUSD"}:
            return (await ibkr_acceptance_service.verify_contract(symbol)).model_dump(mode="json")
        return (await _adapter("ibkr").resolve_contract(str(payload.get("instrument_id") or ""))).model_dump(mode="json")
    except BrokerError as exc:
        raise _safe(exc) from exc


@router.post("/ibkr/read-only-acceptance")
async def ibkr_read_only_acceptance(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await ibkr_acceptance_service.read_only_acceptance()


@router.get("/ibkr/acceptance/status")
async def ibkr_acceptance_status(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    status = await ibkr_acceptance_service.status()
    incidents = [row.model_dump(mode="json") for row in ibkr_acceptance_service.incidents()]
    clean_current = (
        status.get("connection_state") == "ACCOUNT_VERIFIED"
        and status.get("account_verified") is True
        and status.get("reconciliation_status") == "MATCHED"
        and status.get("recovery", {}).get("blocking") is False
    )
    current_incidents = [] if clean_current else [row for row in incidents if row.get("blocking")]
    historical_incidents = incidents if clean_current else [row for row in incidents if not row.get("blocking")]
    return {
        **status,
        "current_blocking_incidents": current_incidents,
        "historical_incidents": historical_incidents,
        "incidents": incidents,
        "result": "READY" if clean_current and not current_incidents else "BLOCKED" if current_incidents else "PARTIAL",
    }


@router.post("/ibkr/acceptance/prepare")
async def ibkr_acceptance_prepare(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    readonly = await ibkr_acceptance_service.read_only_acceptance()
    checklist = {
        "real_ibkr_adapter": readonly["adapter_mode"] == "REAL_IBKR_PAPER" and readonly["state_source"] == "POSTGRESQL",
        "paper_account_verified": readonly["account_verified"],
        "account_allowlisted": readonly["account_verified"],
        "eurusd_contract_verified": bool((readonly.get("contracts", {}).get("EURUSD") or {}).get("usable_for_real_submission")),
        "recovery_complete": not (await ibkr_acceptance_service.status()).get("recovery", {}).get("blocking"),
        "reconciliation_matched": (readonly.get("reconciliation") or {}).get("status") in {"MATCHED", "MATCHED_EMPTY"},
        "emergency_disable_off": True,
        "no_duplicate_order": True,
        "no_existing_eurusd_exposure": True,
    }
    return {"status": "READY" if all(checklist.values()) else "BLOCKED", "checklist": checklist, "read_only_acceptance": readonly}


@router.post("/ibkr/acceptance/fx5e/preview")
async def ibkr_fx5e_preview(payload: dict[str, Any], current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return await ibkr_acceptance_service.create_fx5e_preview(
        side=str(payload.get("side") or "BUY"),
        quantity=str(payload.get("quantity") or "1000"),
    )


@router.post("/ibkr/acceptance/fx5e/approve")
async def ibkr_fx5e_approve(payload: dict[str, Any], current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    result = ibkr_acceptance_service.approve_fx5e_preview(
        preview_id=str(payload.get("preview_id") or ""),
        approval_token=str(payload.get("approval_token") or ""),
    )
    if result.get("status") != "APPROVED":
        raise HTTPException(status_code=409, detail=result)
    return result


@router.post("/ibkr/acceptance/fx5e/submit")
async def ibkr_fx5e_submit(payload: dict[str, Any], current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    result = await ibkr_acceptance_service.submit_fx5e_approved_preview(preview_id=str(payload.get("preview_id") or ""))
    if result.get("status") in {"BLOCKED", "SUBMISSION_UNKNOWN"}:
        raise HTTPException(status_code=409, detail=result)
    return result


@router.post("/ibkr/acceptance/fx5e/close")
async def ibkr_fx5e_close(payload: dict[str, Any], current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    result = await ibkr_acceptance_service.close_fx5e_acceptance_trade(
        preview_id=str(payload.get("preview_id") or ""),
        manual_confirmation=bool(payload.get("manual_confirmation")),
    )
    if result.get("status") == "BLOCKED":
        raise HTTPException(status_code=409, detail=result)
    return result


@router.post("/ibkr/acceptance/execute")
async def ibkr_acceptance_execute(payload: dict[str, Any], current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    prepared = await ibkr_acceptance_prepare(current_user)
    if prepared["status"] != "READY" or not bool(payload.get("manual_confirmation")):
        raise HTTPException(status_code=409, detail={"code": "ACCEPTANCE_BLOCKED", "message": "real IBKR paper acceptance preconditions or manual confirmation are missing", "checklist": prepared["checklist"]})
    raise HTTPException(status_code=409, detail={"code": "REAL_ORDER_SUBMISSION_NOT_IMPLEMENTED", "message": "real IBKR paper order submission requires a verified TWS/Gateway adapter session"})


@router.post("/ibkr/acceptance/exit")
async def ibkr_acceptance_exit(payload: dict[str, Any], current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    raise HTTPException(status_code=409, detail={"code": "REAL_EXIT_NOT_AVAILABLE", "message": "no verified real IBKR acceptance trade is active", "order_id": payload.get("order_id")})


@router.get("/ibkr/acceptance/result")
async def ibkr_acceptance_result(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    status = await ibkr_acceptance_status(current_user)
    return {"result": status["result"], "status": status}


@router.get("/ibkr/contracts/{instrument_id}")
async def get_ibkr_contract(instrument_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return (await _adapter("ibkr").resolve_contract(instrument_id)).model_dump(mode="json")
    except BrokerError as exc:
        raise _safe(exc) from exc


@router.get("/ibkr/quotes/{instrument_id}")
async def ibkr_quote(instrument_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return (await _adapter("ibkr").quote(instrument_id)).model_dump(mode="json")


@router.post("/ibkr/historical-bars")
async def ibkr_historical(payload: HistoricalRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    rows = await _adapter("ibkr").historical_bars(payload.instrument_id, bar_size=payload.bar_size, duration=payload.duration)
    return {"items": [row.model_dump(mode="json") for row in rows]}


@router.get("/ibkr/accounts")
async def ibkr_accounts(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": [row.model_dump(mode="json") for row in await _adapter("ibkr").accounts()]}


@router.get("/ibkr/accounts/{account_id}/snapshot")
async def ibkr_snapshot(account_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return (await _adapter("ibkr").account_snapshot(account_id)).model_dump(mode="json")
    except (BrokerError, KeyError) as exc:
        raise _safe(exc) from exc


@router.get("/ibkr/accounts/{account_id}/cash")
async def ibkr_cash(account_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    snap = await _adapter("ibkr").account_snapshot(account_id)
    return {"items": [row.model_dump(mode="json") for row in snap.cash]}


@router.post("/ibkr/accounts/{account_id}/fx-funding-check")
async def ibkr_fx_funding_check(account_id: str, payload: FxFundingCheckRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    adapter = _adapter("ibkr")
    if not hasattr(adapter, "fx_cash_funding_check"):
        raise HTTPException(status_code=501, detail={"code": "FX_FUNDING_CHECK_UNAVAILABLE", "message": "adapter does not expose FX cash funding checks"})
    command = BrokerOrderCommand(
        canonical_order_id=f"FX_FUNDING_CHECK_{uuid.uuid4().hex[:10]}",
        account_id=account_id,
        instrument_id=payload.instrument_id,
        side=payload.side.upper(),
        order_type=payload.order_type.upper(),
        time_in_force="DAY",
        quantity=payload.quantity,
        approved_quantity=payload.quantity,
        risk_evaluation_id="fx_funding_check",
        idempotency_key=f"FX_FUNDING_CHECK_{uuid.uuid4().hex}",
        correlation_id="FX_FUNDING_CHECK_DRY_RUN",
        user_approval=False,
    )
    try:
        check = await adapter.fx_cash_funding_check(command, price=payload.price)
        return {
            "status": "FUNDED" if check.get("funding_status") == "FUNDED" else "REJECTED_PRE_BROKER" if check.get("funding_status") == "INSUFFICIENT" else "UNKNOWN",
            "check": _json_safe(check),
            "place_order_calls": 0,
        }
    except BrokerError as exc:
        raise _safe(exc) from exc


@router.post("/ibkr/accounts/{account_id}/fx-rearm-readiness")
async def ibkr_fx_rearm_readiness(account_id: str, payload: FxRearmReadinessRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await _build_fx_rearm_readiness(_adapter("ibkr"), account_id, payload)
    except BrokerError as exc:
        raise _safe(exc) from exc


@router.get("/ibkr/accounts/{account_id}/positions")
async def ibkr_positions(account_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": [row.model_dump(mode="json") for row in await _adapter("ibkr").positions(account_id)]}


@router.post("/ibkr/accounts/{account_id}/flatten-fx")
async def ibkr_flatten_fx(account_id: str, payload: FlattenFxRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    if not payload.manual_confirmation:
        raise HTTPException(status_code=403, detail={"code": "MANUAL_CONFIRMATION_REQUIRED", "message": "manual confirmation is required to flatten FX positions"})
    if ibkr_config.mode != "PAPER" or bool(getattr(ibkr_config, "live_trading_enabled", False)) or bool(getattr(ibkr_config, "allow_live", False)):
        raise HTTPException(status_code=403, detail={"code": "PAPER_ONLY_FLATTEN_REQUIRED", "message": "FX flatten is only available for verified IBKR paper mode"})
    adapter = _adapter("ibkr")
    try:
        await adapter.connect()
        positions = await adapter.positions(account_id)
        max_quantity = min(Decimal(str(ai_trading_config().max_position_size_forex)), Decimal("50000"))
        request_id = uuid.uuid4().hex[:10]
        submitted: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for position in positions:
            instrument_id = str(position.instrument_id or "").upper()
            if not instrument_id.startswith("FX:"):
                skipped.append({"instrument_id": instrument_id, "reason": "NON_FX_POSITION"})
                continue
            quantity = Decimal(str(position.quantity or "0"))
            if quantity == 0:
                continue
            side = "BUY" if quantity < 0 else "SELL"
            remaining = abs(quantity)
            chunk_index = 1
            while remaining > 0:
                chunk = min(remaining, max_quantity)
                order_ref = f"IBKR_PAPER_FLATTEN_{request_id}_{instrument_id.replace(':', '')}_{chunk_index}"
                command = BrokerOrderCommand(
                    canonical_order_id=order_ref,
                    account_id=account_id,
                    instrument_id=instrument_id,
                    side=side,
                    order_type="MARKET",
                    time_in_force="DAY",
                    quantity=chunk,
                    approved_quantity=chunk,
                    risk_evaluation_id=f"flatten_{instrument_id.replace(':', '')}",
                    idempotency_key=order_ref,
                    correlation_id=order_ref,
                    user_approval=True,
                )
                receipt = await adapter.submit_paper_fx_flatten_order(command)
                submitted.append({"instrument_id": instrument_id, "side": side, "quantity": str(chunk), "order_ref": order_ref, "receipt": _json_safe(receipt)})
                remaining -= chunk
                chunk_index += 1
        after_positions = await adapter.positions(account_id)
        return {
            "status": "SUBMITTED" if submitted else "NO_OPEN_FX_POSITIONS",
            "reason": payload.reason,
            "submitted": submitted,
            "skipped": skipped,
            "positions_before": [row.model_dump(mode="json") for row in positions],
            "positions_after": [row.model_dump(mode="json") for row in after_positions],
        }
    except BrokerError as exc:
        raise _safe(exc) from exc


@router.get("/ibkr/accounts/{account_id}/orders")
async def ibkr_orders(account_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": [row.model_dump(mode="json") for row in await _adapter("ibkr").open_orders(account_id)]}


@router.post("/ibkr/accounts/{account_id}/orders/{broker_order_id}/cancel")
async def ibkr_cancel_order(account_id: str, broker_order_id: str, payload: CancelBrokerOrderRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    if not payload.manual_confirmation:
        raise HTTPException(status_code=403, detail={"code": "MANUAL_CONFIRMATION_REQUIRED", "message": "manual confirmation is required to cancel broker orders"})
    if ibkr_config.mode != "PAPER" or bool(getattr(ibkr_config, "live_trading_enabled", False)) or bool(getattr(ibkr_config, "allow_live", False)):
        raise HTTPException(status_code=403, detail={"code": "PAPER_ONLY_CANCEL_REQUIRED", "message": "broker order cancellation is only available for verified IBKR paper mode"})
    try:
        receipt = await _adapter("ibkr").cancel_order(
            BrokerCancelCommand(
                canonical_order_id=f"IBKR:{broker_order_id}",
                broker_order_id=broker_order_id,
                account_id=account_id,
                reason=payload.reason,
            )
        )
        return {"status": "CANCEL_SUBMITTED", "receipt": receipt.model_dump(mode="json")}
    except BrokerError as exc:
        raise _safe(exc) from exc


@router.get("/ibkr/accounts/{account_id}/executions")
async def ibkr_executions(account_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": [row.model_dump(mode="json") for row in await _adapter("ibkr").executions(account_id)]}


@router.get("/ibkr/accounts/{account_id}/reconciliation")
async def ibkr_reconciliation(account_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return (await _adapter("ibkr").reconcile(account_id)).model_dump(mode="json")


@router.post("/ibkr/accounts/{account_id}/reconcile")
async def ibkr_reconcile(account_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return (await _adapter("ibkr").reconcile(account_id)).model_dump(mode="json")


async def _build_fx_rearm_readiness(adapter: Any, account_id: str, payload: FxRearmReadinessRequest) -> dict[str, Any]:
    symbol = _normalize_fx_symbol(payload.symbol)
    allowlist = {"EURUSD"}
    quantity = payload.representative_quantity
    max_quantity = payload.max_quantity or Decimal(str(ai_trading_config().max_position_size_forex))
    entry_price = payload.entry_price or Decimal("1.1000")
    long_stop = payload.long_stop_price or (entry_price - Decimal("0.0100"))
    long_target = payload.long_take_profit or (entry_price + Decimal("0.0200"))
    short_stop = payload.short_stop_price or (entry_price + Decimal("0.0100"))
    short_target = payload.short_take_profit or (entry_price - Decimal("0.0200"))
    risk_sized_quantity = min(max_quantity, _risk_sized_quantity(entry_price, long_stop, short_stop))

    positions = await adapter.positions(account_id)
    open_orders = await adapter.open_orders(account_id)
    completed_status, completed_orders = await adapter.completed_orders(account_id) if hasattr(adapter, "completed_orders") else ("UNAVAILABLE", [])
    executions = await adapter.executions(account_id) if hasattr(adapter, "executions") else []
    snapshot = await adapter.account_snapshot(account_id)
    reconciliation = await adapter.reconcile(account_id)

    blocking_reasons: list[str] = []
    live_trading_enabled = bool(getattr(ibkr_config, "live_trading_enabled", False) or getattr(ibkr_config, "allow_live", False))
    if live_trading_enabled:
        _append_once(blocking_reasons, "LIVE_TRADING_ENABLED")
    usdjpy_position = Decimal("0")
    strategy_positions: list[dict[str, Any]] = []
    for position in positions:
        instrument_symbol = _normalize_fx_symbol(str(getattr(position, "instrument_id", "") or getattr(position, "symbol", "")))
        qty = _safe_decimal(getattr(position, "quantity", "0"))
        if instrument_symbol == "USDJPY":
            usdjpy_position += qty
        if instrument_symbol and qty != 0:
            strategy_positions.append({"symbol": instrument_symbol, "quantity": str(qty), "instrument_id": getattr(position, "instrument_id", "")})
            if instrument_symbol not in allowlist:
                _append_once(blocking_reasons, "NON_ALLOWLISTED_FX_POSITION_OPEN")
    if usdjpy_position != 0:
        _append_once(blocking_reasons, "USDJPY_POSITION_OPEN")

    usdjpy_orders = [_order for _order in open_orders if _order_matches_symbol(_order, "USDJPY")]
    if usdjpy_orders:
        _append_once(blocking_reasons, "OPEN_USDJPY_ORDER")
    if any(str(getattr(order, "broker_order_id", "")) == "19" for order in usdjpy_orders):
        _append_once(blocking_reasons, "ORDER_19_OPEN")
    inactive_usdjpy_orders = [
        order
        for order in list(open_orders) + list(completed_orders)
        if _order_matches_symbol(order, "USDJPY") and str(getattr(order, "raw_status", "") or getattr(order, "state", "")).upper() in {"INACTIVE", "REJECTED", "ERROR"}
    ]
    if inactive_usdjpy_orders:
        _append_once(blocking_reasons, "USDJPY_CHILD_ORDER_REQUIRES_ACTION")

    usdjpy_executions = [execution for execution in executions if _execution_matches_symbol(execution, "USDJPY")]
    unresolved_usdjpy_executions = [execution for execution in usdjpy_executions if _safe_decimal(getattr(execution, "commission", "0")) == 0]
    if unresolved_usdjpy_executions:
        _append_once(blocking_reasons, "UNRESOLVED_USDJPY_EXECUTION")
        _append_once(blocking_reasons, "UNRESOLVED_USDJPY_COMMISSION")

    if reconciliation.status not in {"MATCHED", "MATCHED_EMPTY"}:
        _append_once(blocking_reasons, "RECONCILIATION_NOT_MATCHED")

    funding = {
        "representative_quantity": await _eurusd_lifecycle_funding(adapter, account_id, symbol, quantity, entry_price, long_stop, long_target, short_stop, short_target),
        "risk_sized_quantity": await _eurusd_lifecycle_funding(adapter, account_id, symbol, risk_sized_quantity, entry_price, long_stop, long_target, short_stop, short_target),
        "configured_max_quantity": await _eurusd_lifecycle_funding(adapter, account_id, symbol, max_quantity, entry_price, long_stop, long_target, short_stop, short_target),
    }
    for label, result in funding.items():
        if not result["full_lifecycle_fundable"]:
            _append_once(blocking_reasons, f"{label.upper()}_PROTECTIVE_EXIT_NOT_FUNDABLE")
            _append_once(blocking_reasons, "PROTECTIVE_EXIT_NOT_FUNDABLE")
    if funding["risk_sized_quantity"]["buy_lifecycle_fundable"] != funding["risk_sized_quantity"]["sell_lifecycle_fundable"]:
        _append_once(blocking_reasons, "DIRECTIONAL_LIFECYCLE_NOT_FULLY_FUNDED")

    buy_lifecycle_capacity = _direction_lifecycle_quantity(funding["configured_max_quantity"], "buy")
    sell_lifecycle_capacity = _direction_lifecycle_quantity(funding["configured_max_quantity"], "sell")
    max_funded_position_size = min(max_quantity, buy_lifecycle_capacity, sell_lifecycle_capacity, risk_sized_quantity)
    settlement_cash_buffer = _settlement_cash_buffer(funding["configured_max_quantity"], max_funded_position_size)
    ready = not blocking_reasons
    return {
        "READY_TO_REARM": ready,
        "blocking_reasons": blocking_reasons,
        "recommended_AI_ORDER_SUBMISSION_ENABLED": "1" if ready else "0",
        "recommended_symbol_allowlist": "EURUSD",
        "recommended_maximum_funded_position_size": str(max_funded_position_size),
        "recommended_max_fully_funded_quantity": str(max_funded_position_size),
        "buy_lifecycle_funded_quantity": str(buy_lifecycle_capacity),
        "sell_lifecycle_funded_quantity": str(sell_lifecycle_capacity),
        "risk_sized_quantity": str(risk_sized_quantity),
        "settlement_cash_buffer_after_maximum_quantity": _json_safe(settlement_cash_buffer),
        "place_order_calls": 0,
        "live_trading_enabled": live_trading_enabled,
        "auto_fx_conversion_for_trading": False,
        "usdjpy": {
            "position": str(usdjpy_position),
            "open_orders": [row.model_dump(mode="json") for row in usdjpy_orders],
            "inactive_or_rejected_orders": [row.model_dump(mode="json") for row in inactive_usdjpy_orders],
            "executions": [row.model_dump(mode="json") for row in usdjpy_executions],
            "unresolved_executions": [row.model_dump(mode="json") for row in unresolved_usdjpy_executions],
        },
        "broker_state": {
            "positions": [row.model_dump(mode="json") for row in positions],
            "strategy_positions": strategy_positions,
            "open_orders": [row.model_dump(mode="json") for row in open_orders],
            "completed_orders_status": completed_status,
            "completed_orders": [row.model_dump(mode="json") for row in completed_orders],
            "executions": [row.model_dump(mode="json") for row in executions],
            "currency_cash_balances": [row.model_dump(mode="json") for row in snapshot.cash],
            "account_capability": _account_capability(snapshot),
            "reconciliation": reconciliation.model_dump(mode="json"),
        },
        "eurusd_preflight": funding,
    }


async def _eurusd_lifecycle_funding(
    adapter: Any,
    account_id: str,
    symbol: str,
    quantity: Decimal,
    entry_price: Decimal,
    long_stop: Decimal,
    long_target: Decimal,
    short_stop: Decimal,
    short_target: Decimal,
) -> dict[str, Any]:
    buy_command = _funding_command(account_id, symbol, "BUY", quantity)
    sell_command = _funding_command(account_id, symbol, "SELL", quantity)
    buy = {
        "entry": await adapter.fx_cash_funding_check(buy_command, price=entry_price, role="entry"),
        "stop_loss": await adapter.fx_cash_funding_check(buy_command, side="SELL", price=long_stop, role="stop_loss"),
        "take_profit": await adapter.fx_cash_funding_check(buy_command, side="SELL", price=long_target, role="take_profit"),
        "emergency_close": await adapter.fx_cash_funding_check(buy_command, side="SELL", price=entry_price, role="emergency_close"),
    }
    sell = {
        "entry": await adapter.fx_cash_funding_check(sell_command, price=entry_price, role="entry"),
        "stop_loss": await adapter.fx_cash_funding_check(sell_command, side="BUY", price=short_stop, role="stop_loss"),
        "take_profit": await adapter.fx_cash_funding_check(sell_command, side="BUY", price=short_target, role="take_profit"),
        "emergency_close": await adapter.fx_cash_funding_check(sell_command, side="BUY", price=entry_price, role="emergency_close"),
    }
    return {
        "quantity": str(quantity),
        "entry_price": str(entry_price),
        "buy": _json_safe(buy),
        "sell": _json_safe(sell),
        "buy_lifecycle_fundable": _all_funded(buy),
        "sell_lifecycle_fundable": _all_funded(sell),
        "full_lifecycle_fundable": _all_funded(buy) and _all_funded(sell),
        "place_order_calls": 0,
    }


def _funding_command(account_id: str, symbol: str, side: str, quantity: Decimal) -> BrokerOrderCommand:
    order_id = f"FX_REARM_CHECK_{uuid.uuid4().hex[:10]}"
    return BrokerOrderCommand(
        canonical_order_id=order_id,
        account_id=account_id,
        instrument_id=f"FX:{symbol}",
        side=side,
        order_type="MARKET",
        time_in_force="DAY",
        quantity=quantity,
        approved_quantity=quantity,
        risk_evaluation_id="fx_rearm_readiness",
        idempotency_key=order_id,
        correlation_id="FX_REARM_READINESS_DRY_RUN",
        user_approval=False,
    )


def _all_funded(checks: dict[str, dict[str, Any]]) -> bool:
    return all((row or {}).get("funding_status") == "FUNDED" for row in checks.values())


def _direction_lifecycle_quantity(result: dict[str, Any], side: str) -> Decimal:
    checks = result.get(side) or {}
    values = [
        _safe_decimal(check.get("maximum_affordable_quantity"))
        for check in checks.values()
        if isinstance(check, dict) and check.get("maximum_affordable_quantity") is not None
    ]
    return min(values) if values else Decimal("0")


def _settlement_cash_buffer(result: dict[str, Any], quantity: Decimal) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for side in ("buy", "sell"):
        out[side] = {}
        for role, check in (result.get(side) or {}).items():
            if not isinstance(check, dict):
                continue
            available = _safe_decimal(check.get("available_cash"))
            maximum = _safe_decimal(check.get("maximum_affordable_quantity"))
            if quantity <= maximum:
                out[side][role] = {
                    "required_currency": check.get("required_currency"),
                    "available_cash": str(available),
                    "maximum_affordable_quantity": str(maximum),
                    "buffer_quantity_units": str(maximum - quantity),
                }
    return out


def _risk_sized_quantity(entry_price: Decimal, long_stop: Decimal, short_stop: Decimal) -> Decimal:
    cfg = ai_trading_config()
    stop_distance = max(abs(entry_price - long_stop), abs(short_stop - entry_price), Decimal("0.0001"))
    risk_cap = min(Decimal(str(cfg.max_trade_loss_usd)), Decimal(str(cfg.max_total_open_risk_usd)))
    try:
        return max(Decimal("0"), (risk_cap / stop_distance).quantize(Decimal("1"), rounding=ROUND_FLOOR))
    except Exception:
        return Decimal("0")


def _minimum_affordable_quantity(result: dict[str, Any]) -> Decimal:
    values: list[Decimal] = []
    for side in ("buy", "sell"):
        for check in (result.get(side) or {}).values():
            if isinstance(check, dict) and check.get("maximum_affordable_quantity") is not None:
                values.append(_safe_decimal(check.get("maximum_affordable_quantity")))
    return min(values) if values else Decimal("0")


def _account_capability(snapshot: Any) -> dict[str, Any]:
    cash = {str(row.currency).upper(): str(row.available_cash) for row in getattr(snapshot, "cash", [])}
    return {
        "account_type": str(getattr(snapshot, "account_type", "CASH") or "CASH"),
        "base_currency": next(iter(cash.keys()), "USD"),
        "available_funds": str(getattr(snapshot, "available_funds", "0")),
        "excess_liquidity": str(getattr(snapshot, "excess_liquidity", "0")),
        "forex_leverage_permitted": str(getattr(ibkr_config, "mode", "")).upper() == "MARGIN",
        "available_cash_by_currency": cash,
    }


def _order_matches_symbol(order: Any, symbol: str) -> bool:
    haystack = " ".join(
        [
            str(getattr(order, "instrument_id", "")),
            str(getattr(order, "correlation_id", "")),
            str(getattr(order, "canonical_order_id", "")),
            str(getattr(order, "broker_order_id", "")),
        ]
    ).upper()
    return symbol.upper() in haystack


def _execution_matches_symbol(execution: Any, symbol: str) -> bool:
    return symbol.upper() in str(getattr(execution, "instrument_id", "")).upper()


def _normalize_fx_symbol(value: str) -> str:
    raw = str(value or "").upper().replace("FX:", "").replace("/", "")
    return raw


def _safe_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


def _append_once(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _adapter(name: str):
    try:
        return broker_registry.get(name)
    except BrokerError as exc:
        raise _safe(exc) from exc


def _tcp_reachable(host: str, port: int, timeout_seconds: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    return str(value)


def _safe(exc: Exception) -> HTTPException:
    if isinstance(exc, BrokerError):
        return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})
    return HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "not found"})
