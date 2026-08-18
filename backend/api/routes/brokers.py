from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.auth.deps import get_current_user
from backend.brokers import broker_registry
from backend.brokers.errors import BrokerError
from backend.brokers.mt5.autonomous import mt5_autonomous_service
from backend.brokers.mt5 import account_registry
from backend.brokers.mt5.confidence_calibration import (
    calibration_reliability_report,
    component_effectiveness_report,
    confidence_band_report,
    ranking_quality_report,
    recent_candidate_outcomes,
    rejection_analysis_report,
    threshold_simulation_report,
)
from backend.brokers.mt5.config import mt5_config
from backend.brokers.mt5.multi_account import adapter_for_account, config_for_profile
from backend.brokers.mt5.prop_state import evaluate_entry_protection
from backend.shared.db import SessionLocal
from backend.brokers.mt5.risk_calculator import calculate_canonical_loss_per_lot
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
from backend.models.user import User

router = APIRouter(prefix="/api/brokers", tags=["brokers"])


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


@router.get("/mt5/accounts")
async def mt5_accounts(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    registry = account_registry.registry_health()
    registry["items"] = [await _mt5_account_health(item["account_id"]) for item in registry["items"]]
    return registry


@router.get("/mt5/accounts/{account_id}/prop-status")
async def mt5_account_prop_status(account_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    profile = account_registry.profile_by_id(account_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="unknown account_id")
    if profile.prop_profile not in {"FTMO_2_STEP"}:
        return {
            "account": account_registry.profile_health(profile),
            "prop_status": {
                "account_id": account_id,
                "prop_profile": profile.prop_profile,
                "challenge_status": "DISABLED",
                "message": "non-prop demo account",
                "new_entries_allowed": profile.enabled,
            },
        }
    live = await _live_mt5_account_values(profile)
    balance = Decimal(str(live.get("balance") if live.get("balance") is not None else profile.expected_initial_balance))
    equity = Decimal(str(live.get("equity") if live.get("equity") is not None else profile.expected_initial_balance))
    with SessionLocal() as db:
        protection = evaluate_entry_protection(db, account_id, config_for_profile(profile), balance=balance, equity=equity)
    return {
        "account": account_registry.profile_health(profile),
        "prop_status": protection["challenge_status"],
        "daily_state": {
            "trading_day": protection["trading_day"],
            "reset_timezone": protection["reset_timezone"],
            "day_start_balance": str(protection["day_start_balance"]),
            "day_start_equity": str(protection["day_start_equity"]),
            "realized_pnl_today": str(protection["realized_pnl_today"]),
            "commission_today": str(protection["commission_today"]),
            "swap_today": str(protection["swap_today"]),
            "fee_today": str(protection["fee_today"]),
            "floating_pnl": str(protection["floating_pnl"]),
            "daily_pnl_total": str(protection["daily_pnl_total"]),
        },
        "entry_block_reasons": protection["entry_block_reasons"],
    }


async def _mt5_account_health(account_id: str) -> dict[str, Any]:
    profile = account_registry.profile_by_id(account_id)
    if profile is None:
        return {"account_id": account_id, "health": "BLOCKED", "blockers": ["UNKNOWN_ACCOUNT"]}
    base = account_registry.profile_health(profile)
    base["masked_login"] = base.pop("expected_login_masked", None)
    base["bridge_health"] = "DISABLED" if not profile.enabled else "UNKNOWN"
    base["terminal_health"] = "DISABLED" if not profile.enabled else "UNKNOWN"
    base["connected"] = False
    base["account_classification"] = profile.account_mode
    base["balance"] = None
    base["equity"] = None
    base["floating_pnl"] = None
    base["daily_pnl"] = None
    base["open_positions"] = None
    base["open_risk"] = None
    base["adaptive_manager_status"] = "ISOLATED_BY_ACCOUNT_CONTEXT"
    autonomous_status = mt5_autonomous_service.account_status(account_id) if hasattr(mt5_autonomous_service, "account_status") else mt5_autonomous_service.status()
    base["autonomous_scheduler"] = {
        "scheduler_running": bool(autonomous_status.get("scheduler_running")) if autonomous_status else False,
        "current_state": autonomous_status.get("current_state") if autonomous_status else None,
        "last_cycle_time": autonomous_status.get("last_cycle_time") if autonomous_status else None,
        "next_cycle_time": autonomous_status.get("next_cycle_time") if autonomous_status else None,
        "last_result": autonomous_status.get("last_result") if autonomous_status else None,
        "emergency_disable": bool(autonomous_status.get("emergency_disable")) if autonomous_status else False,
    }
    if not profile.enabled:
        snapshot = _prop_snapshot(profile, balance=profile.expected_initial_balance, equity=profile.expected_initial_balance)
        base["current_prop_status"] = snapshot["prop_status"]
        base["entry_block_reasons"] = snapshot["entry_block_reasons"]
        return base
    live = await _live_mt5_account_values(profile)
    base.update({key: live.get(key) for key in ("connected", "balance", "equity", "floating_pnl", "daily_pnl", "open_positions", "open_risk")})
    base["server"] = live.get("server") or profile.expected_server
    base["terminal_health"] = "OK" if live.get("connected") else "DISCONNECTED"
    base["bridge_health"] = "OK" if live.get("connected") else "DISCONNECTED"
    base["blockers"] = sorted(set((base.get("blockers") or []) + (live.get("blockers") or [])))
    base["health"] = "BLOCKED" if base["blockers"] else ("OK" if live.get("connected") else "DEGRADED")
    snapshot = _prop_snapshot(
        profile,
        balance=Decimal(str(live.get("balance") if live.get("balance") is not None else profile.expected_initial_balance)),
        equity=Decimal(str(live.get("equity") if live.get("equity") is not None else profile.expected_initial_balance)),
    )
    base["current_prop_status"] = snapshot["prop_status"]
    base["entry_block_reasons"] = snapshot["entry_block_reasons"]
    if live.get("connected"):
        base["daily_pnl"] = str(snapshot["daily_pnl_total"])
    return base


async def _live_mt5_account_values(profile: account_registry.MT5AccountProfile) -> dict[str, Any]:
    try:
        adapter = _adapter("mt5") if profile.account_id == "demo_10k" else adapter_for_account(profile.account_id)
        account = await adapter.mt5_account()
        blockers = account_registry.validate_profile_account(profile, account)
        positions = await adapter.mt5_positions()
        floating = sum(Decimal(str(row.profit or 0)) + Decimal(str(row.swap or 0)) for row in positions)
        return {
            "connected": True,
            "server": account.server,
            "balance": str(account.balance),
            "equity": str(account.equity),
            "floating_pnl": str(floating),
            "daily_pnl": None,
            "open_positions": len(positions),
            "open_risk": None,
            "blockers": blockers,
        }
    except Exception as exc:
        return {"connected": False, "blockers": [f"ACCOUNT_BRIDGE_UNAVAILABLE:{exc.__class__.__name__}"]}


def _prop_snapshot(profile: account_registry.MT5AccountProfile, *, balance: Decimal, equity: Decimal) -> dict[str, Any]:
    if profile.prop_profile != "FTMO_2_STEP":
        return {
            "prop_status": {"account_id": profile.account_id, "prop_profile": profile.prop_profile, "challenge_status": "DISABLED", "new_entries_allowed": profile.enabled},
            "entry_block_reasons": [],
            "daily_pnl_total": Decimal("0"),
        }
    with SessionLocal() as db:
        protection = evaluate_entry_protection(db, profile.account_id, config_for_profile(profile), balance=balance, equity=equity)
    return {
        "prop_status": protection["challenge_status"],
        "entry_block_reasons": protection["entry_block_reasons"],
        "daily_pnl_total": protection["daily_pnl_total"],
    }


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


@router.get("/mt5/risk-diagnostics/{symbol}")
async def mt5_risk_diagnostics(symbol: str, entry: float | None = None, stop: float | None = None, direction: str = "LONG", current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """PART 16: exposes the canonical risk calculator's (backend/brokers/mt5/risk_calculator.py)
    full diagnostic -- broker-native/contract/tick estimates, the selected (most conservative)
    one, disagreement %, and block/warning state -- for any symbol, without needing an open
    position or a live sizing attempt. `entry`/`stop` default to the current bid/ask and a
    symbol-scaled 1% probe distance when omitted, so this is usable as a pure "is this symbol's
    broker metadata trustworthy right now" health check. No credentials in the response --
    symbol_info/estimates never carry account secrets."""
    adapter = _adapter("mt5")
    symbol_info = await adapter.symbol_info(symbol.upper())
    quote = await adapter.latest_tick(symbol.upper())
    entry_price = Decimal(str(entry)) if entry is not None else Decimal(str(quote.bid or quote.ask or 0))
    if stop is not None:
        stop_price = Decimal(str(stop))
    else:
        probe = (symbol_info.point or Decimal("0.0001")) * 100
        stop_price = entry_price - probe if direction.upper() == "LONG" else entry_price + probe
    try:
        mt5_client = adapter.client.ensure_ready()
    except Exception:
        mt5_client = None
    cfg = mt5_config()
    account = await adapter.mt5_account()
    result = await calculate_canonical_loss_per_lot(
        direction=direction.upper(), entry=entry_price, stop=stop_price, symbol_info=symbol_info,
        mt5_client=mt5_client, warning_pct=cfg.risk_calculation_disagreement_pct, critical_pct=cfg.risk_calculation_critical_disagreement_pct,
        account_currency=account.currency or "USD", adapter=adapter,
    )
    return {"symbol": symbol.upper(), "direction": direction.upper(), "entry": str(entry_price), "stop": str(stop_price), **result.to_dict()}


@router.get("/mt5/risk-diagnostics-universe")
async def mt5_risk_diagnostics_universe(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Part 12: read-only diagnostic audit of EVERY symbol in the current MT5 forex universe's
    risk-sizing metadata (order_calc_profit/contract_size/tick_value agreement, currency-
    conversion resolvability, quorum) -- makes hidden EURJPY/XAUUSD-style issues visible before
    any strategy attempts a trade on a symbol. Same computation the autonomous cycle's periodic
    health check (MT5AutonomousTradingService.refresh_risk_metadata_health) uses; this route
    also refreshes that cache so unhealthy symbols are excluded from execution eligibility going
    forward."""
    return await mt5_autonomous_service.refresh_risk_metadata_health()


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


# --- Confidence Validation & Calibration layer (read-only analytics; Part 12). These never
# read or write MT5Config.min_trade_confidence, confidence weights, or any trading-decision
# path -- they only query the append-only mt5_candidate_evaluations dataset. ---


@router.get("/mt5/confidence/overview")
async def mt5_confidence_overview(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"bands": confidence_band_report(), "reliability": calibration_reliability_report()}


@router.get("/mt5/confidence/bands")
async def mt5_confidence_bands(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return confidence_band_report()


@router.get("/mt5/confidence/reliability")
async def mt5_confidence_reliability(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return calibration_reliability_report()


@router.get("/mt5/confidence/components")
async def mt5_confidence_components(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": component_effectiveness_report()}


@router.get("/mt5/confidence/ranking")
async def mt5_confidence_ranking(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return ranking_quality_report()


@router.get("/mt5/confidence/rejections")
async def mt5_confidence_rejections(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": rejection_analysis_report()}


@router.get("/mt5/confidence/threshold-simulation")
async def mt5_confidence_threshold_simulation(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Analytics-only simulation of alternative confidence thresholds over historical
    candidate evaluations. Never reads or changes the live production threshold (75, set via
    MT5Config.min_trade_confidence) -- see backend/brokers/mt5/confidence_calibration.py."""
    return {"items": threshold_simulation_report(), "production_threshold": mt5_config().min_trade_confidence}


@router.get("/mt5/confidence/candidate-outcomes")
async def mt5_confidence_candidate_outcomes(limit: int = 50, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": recent_candidate_outcomes(limit=max(1, min(500, limit)))}


# --- Multi-strategy activation, circuit breaker, and per-strategy analytics (Parts 15-17).
# Config/API visibility only -- no web UI, per the spec ("Config/API visibility is enough").
# strategy_performance/family-performance/participation are read-only analytics over the same
# append-only mt5_candidate_evaluations dataset the confidence-calibration routes above use. ---


@router.get("/mt5/strategies/activation")
async def mt5_strategies_activation(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    from backend.mt5_strategies.models import STRATEGY_FAMILIES, activation_status, multi_strategy_enabled

    return {
        "multi_strategy_enabled": multi_strategy_enabled(),
        "strategies": {
            strategy_id: {"family": meta["family"], "default_activation": meta["default_activation"], "effective_activation": activation_status(strategy_id), "regimes": list(meta["regimes"])}
            for strategy_id, meta in STRATEGY_FAMILIES.items()
        },
    }


@router.get("/mt5/strategies/circuit-breaker")
async def mt5_strategies_circuit_breaker(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    from backend.mt5_strategies.circuit_breaker import circuit_breaker_status

    return circuit_breaker_status()


@router.post("/mt5/strategies/circuit-breaker/{strategy_id}/reset")
async def mt5_strategies_circuit_breaker_reset(strategy_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    from backend.mt5_strategies.circuit_breaker import reset

    reset(strategy_id)
    return {"status": "reset", "strategy_id": strategy_id}


@router.get("/mt5/strategies/performance")
async def mt5_strategies_performance(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    from backend.mt5_strategies.analytics import strategy_performance_report

    return strategy_performance_report()


@router.get("/mt5/strategies/family-performance")
async def mt5_strategies_family_performance(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    from backend.mt5_strategies.analytics import strategy_family_performance_report

    return strategy_family_performance_report()


@router.get("/mt5/strategies/participation")
async def mt5_strategies_participation(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    from backend.mt5_strategies.analytics import contribution_participation_report

    return contribution_participation_report()


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


def _adapter(name: str):
    try:
        return broker_registry.get(name)
    except BrokerError as exc:
        raise _safe(exc) from exc


def _safe(exc: Exception) -> HTTPException:
    if isinstance(exc, BrokerError):
        return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})
    return HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "not found"})


