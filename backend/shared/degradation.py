"""Phase 9 (Forex/MT5 roadmap): a single, explicit place that classifies every subsystem this
platform depends on as HARD_REQUIRED / SOFT_REQUIRED / OPTIONAL, and reports its REAL current
state -- rather than leaving "is this degraded, and does it matter" implicit across a dozen
independently fail-open/fail-closed call sites.

This module does not introduce any new gating behavior. Every gate it reports on already exists
and already enforces its own fail-open/fail-closed policy (see each component's docstring for the
real, pre-existing enforcement point). This is read-only aggregation for observability (Phase 13's
dashboard) and diagnosis -- it answers "what's degraded right now" in one call instead of an
operator having to know which of a dozen modules to check.

Tiers:
  HARD_REQUIRED -- trading for the affected scope (one account, or the whole platform) is not
                   safe without this. Broker identity and portfolio/prop protection are HARD per
                   account (see can_open_new_trade's existing fail-closed enforcement); Postgres
                   is HARD globally (nothing here is durable without it).
  SOFT_REQUIRED -- degrades decision quality or blocks a specific piece of functionality (e.g. new
                   entries for one account) but does not itself make continued operation unsafe.
                   Reconciliation-watchdog untrustworthy state and the Forex Factory economic
                   calendar/news providers are SOFT.
  OPTIONAL      -- a real fallback already exists and is exercised in practice. Redis (falls back
                   to a live MT5/Postgres fetch on every call site -- see redis_layer's own
                   docstring) and Historical Intelligence (OFF/SHADOW never influence a live
                   decision at all) are OPTIONAL.

States: HEALTHY / DEGRADED / UNAVAILABLE (a 3-value simplification of provider_health's 5-value
vocabulary for providers that have finer STALE/SCHEMA_CHANGED distinctions -- see
_economic_provider_component, which maps those down for this module's uniform shape)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

TIER_HARD = "HARD_REQUIRED"
TIER_SOFT = "SOFT_REQUIRED"
TIER_OPTIONAL = "OPTIONAL"

STATE_HEALTHY = "HEALTHY"
STATE_DEGRADED = "DEGRADED"
STATE_UNAVAILABLE = "UNAVAILABLE"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _component(name: str, tier: str, state: str, detail: str, *, account_id: str | None = None) -> dict[str, Any]:
    return {"name": name, "tier": tier, "state": state, "detail": detail, "account_id": account_id}


async def _postgres_component() -> dict[str, Any]:
    """HARD globally -- nothing in this platform (execution records, adaptive state, portfolio
    snapshots, reconciliation findings) is durable without it. Real connectivity check, not a
    config-presence check."""
    from sqlalchemy import text

    from backend.shared.db import SessionLocal

    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return _component("postgres", TIER_HARD, STATE_HEALTHY, "connectivity check succeeded")
    except Exception as exc:
        return _component("postgres", TIER_HARD, STATE_UNAVAILABLE, f"connectivity check failed: {exc.__class__.__name__}")


async def _redis_component() -> dict[str, Any]:
    """OPTIONAL -- every redis_layer call site already falls back to a live MT5/Postgres fetch on
    a cache miss or client failure (see backend/mt5_strategies/redis_layer.py's own docstring);
    this only reports whether that fallback is CURRENTLY in effect."""
    from backend.mt5_strategies import redis_layer

    client = redis_layer.get_client()
    if client is None:
        return _component("redis", TIER_OPTIONAL, STATE_UNAVAILABLE, "no client -- every caller is on the DB/broker fallback path")
    try:
        await client.ping()
        return _component("redis", TIER_OPTIONAL, STATE_HEALTHY, "ping succeeded")
    except Exception as exc:
        return _component("redis", TIER_OPTIONAL, STATE_DEGRADED, f"ping failed: {exc.__class__.__name__}")


def _historical_intelligence_component() -> dict[str, Any]:
    """OPTIONAL by construction -- OFF/SHADOW never influence a live decision at all (see
    backend/historical_intelligence/modes.py's own docstring), and even DEMO_ACTIVE only
    influences a decision once its own trust/reliability/edge-stability gates independently
    clear. There is no state in which this component being unavailable makes trading unsafe."""
    from backend.historical_intelligence import modes

    mode = modes.current_mode()
    state = STATE_HEALTHY if mode is not modes.HistoricalIntelligenceMode.OFF else STATE_DEGRADED
    detail = f"mode={mode.value}" + ("" if mode is not modes.HistoricalIntelligenceMode.OFF else " -- computed but never consulted by any live decision")
    return _component("historical_intelligence", TIER_OPTIONAL, state, detail)


def _economic_provider_component() -> dict[str, Any]:
    """SOFT globally -- calendar_guard already downgrades entries to REDUCE_SIZE/DELAY on
    STALE/DEGRADED (see provider_health.entry_allowed_for_state's own docstring) rather than
    halting trading outright, so a degraded calendar/news feed narrows decision quality without
    making continued trading itself unsafe."""
    from backend.economic_intelligence import provider_health
    from backend.economic_intelligence.config import economic_intelligence_config

    try:
        snapshot = provider_health.health_snapshot(economic_intelligence_config())
        items = snapshot.get("items") or []
        if not items:
            return _component("economic_calendar", TIER_SOFT, STATE_UNAVAILABLE, "no provider state recorded yet")
        worst = max(items, key=lambda row: _PROVIDER_STATE_RANK.get(row.get("state"), 0))
        state = _PROVIDER_STATE_MAP.get(worst.get("state"), STATE_UNAVAILABLE)
        detail = ", ".join(f"{row['provider']}={row['state']}" for row in items)
        return _component("economic_calendar", TIER_SOFT, state, detail)
    except Exception as exc:
        return _component("economic_calendar", TIER_SOFT, STATE_UNAVAILABLE, f"health snapshot failed: {exc.__class__.__name__}")


_PROVIDER_STATE_RANK = {"HEALTHY": 0, "STALE": 1, "DEGRADED": 2, "SCHEMA_CHANGED": 3, "UNAVAILABLE": 4}
_PROVIDER_STATE_MAP = {"HEALTHY": STATE_HEALTHY, "STALE": STATE_DEGRADED, "DEGRADED": STATE_DEGRADED, "SCHEMA_CHANGED": STATE_UNAVAILABLE, "UNAVAILABLE": STATE_UNAVAILABLE}


async def _broker_identity_component(account_id: str) -> dict[str, Any]:
    """HARD per account -- every existing safety gate (position sizing, prop-limit checks,
    reconciliation itself) assumes a real, current account identity/balance/equity read. A
    failure here is scoped to this one account only (adapter_for_account is already isolated per
    account; a failure never raises into or blocks any other account's cycle)."""
    from backend.brokers.mt5.multi_account import adapter_for_account

    try:
        adapter = adapter_for_account(account_id)
        account = await adapter.mt5_account()
        return _component("broker_identity", TIER_HARD, STATE_HEALTHY, f"login={account.login} server={account.server}", account_id=account_id)
    except Exception as exc:
        return _component("broker_identity", TIER_HARD, STATE_UNAVAILABLE, f"mt5_account() failed: {exc.__class__.__name__}", account_id=account_id)


def _portfolio_protection_component(account_id: str) -> dict[str, Any]:
    """HARD per account -- reuses portfolio_manager.can_open_new_trade's ALREADY fail-closed
    enforcement (PORTFOLIO_STATE_UNAVAILABLE/PORTFOLIO_STATE_STALE) rather than reimplementing
    it; this only surfaces that existing verdict in this module's uniform shape."""
    from backend.portfolio_execution.service import portfolio_manager

    try:
        allowed, blockers = portfolio_manager.can_open_new_trade(account_id)
        if allowed:
            return _component("portfolio_protection", TIER_HARD, STATE_HEALTHY, "no blockers", account_id=account_id)
        state = STATE_UNAVAILABLE if "PORTFOLIO_STATE_UNAVAILABLE" in blockers or "PORTFOLIO_STATE_STALE" in blockers else STATE_DEGRADED
        return _component("portfolio_protection", TIER_HARD, state, f"blockers={blockers}", account_id=account_id)
    except Exception as exc:
        return _component("portfolio_protection", TIER_HARD, STATE_UNAVAILABLE, f"can_open_new_trade() failed: {exc.__class__.__name__}", account_id=account_id)


def _reconciliation_component(account_id: str) -> dict[str, Any]:
    """SOFT per account -- reuses reconciliation_watchdog.is_account_state_trustworthy's ALREADY
    fail-closed read (blocks new entries only for this account; see that module's own docstring
    for why this never auto-closes a position or touches other accounts)."""
    from backend.brokers.mt5.reconciliation_watchdog import is_account_state_trustworthy

    try:
        trustworthy = is_account_state_trustworthy(account_id)
        state = STATE_HEALTHY if trustworthy else STATE_DEGRADED
        detail = "trustworthy" if trustworthy else "untrustworthy or never checked -- new entries blocked for this account"
        return _component("reconciliation", TIER_SOFT, state, detail, account_id=account_id)
    except Exception as exc:
        return _component("reconciliation", TIER_SOFT, STATE_UNAVAILABLE, f"check failed: {exc.__class__.__name__}", account_id=account_id)


async def account_snapshot(account_id: str) -> dict[str, Any]:
    """One account's full component list plus a `trading_safe` verdict (all HARD_REQUIRED
    components HEALTHY). SOFT/OPTIONAL components never affect `trading_safe` -- they degrade
    functionality (fewer entries, lower decision quality), not safety."""
    components = [
        await _broker_identity_component(account_id),
        _portfolio_protection_component(account_id),
        _reconciliation_component(account_id),
    ]
    trading_safe = all(c["state"] == STATE_HEALTHY for c in components if c["tier"] == TIER_HARD)
    return {"account_id": account_id, "components": components, "trading_safe": trading_safe}


async def system_snapshot(account_ids: list[str] | None = None) -> dict[str, Any]:
    """Full platform snapshot: global (non-account-scoped) components plus one isolated
    account_snapshot per account -- explicitly proving one account's degraded/unsafe state never
    contaminates another's verdict (Phase 3/Phase 9's shared isolation requirement)."""
    if account_ids is None:
        from backend.brokers.mt5.account_registry import configured_profiles

        account_ids = [profile.account_id for profile in configured_profiles() if profile.enabled]

    global_components = [
        await _postgres_component(),
        await _redis_component(),
        _historical_intelligence_component(),
        _economic_provider_component(),
    ]
    global_safe = all(c["state"] == STATE_HEALTHY for c in global_components if c["tier"] == TIER_HARD)
    accounts = {account_id: await account_snapshot(account_id) for account_id in account_ids}
    return {
        "generated_at": utcnow().isoformat(),
        "global_components": global_components,
        "global_safe": global_safe,
        "accounts": accounts,
        "accounts_safe": {account_id: snap["trading_safe"] for account_id, snap in accounts.items()},
    }
