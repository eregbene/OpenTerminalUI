"""Gate D -- "same Bensim engine drives MT5 or cTrader" bridge (2026-08-27).

Deliberately NOT a second scheduler, NOT a second strategy registry, NOT a re-evaluation of
signals from scratch. This module is invoked from the LIVE MT5 cycle (backend/brokers/mt5/
autonomous.py::run_cycle, immediately after the demo_10k account's own MT5 submission decision
is fully finalized) with the SAME naturally-generated `best` candidate and the SAME confidence
score that already passed strategy validity -> confidence -> Historical Intelligence -> economic
guard -> portfolio guard -> trade-frequency -> losing-streak -> concurrent-exposure -> stale-RR
checks for MT5. Nothing here can produce or force a signal; a signal that never qualified for
MT5 never reaches this bridge either.

This IS the temporary, explicitly-flagged market-data shortcut the user's own design
instructions sanction ("use the existing Bensim/MT5-derived strategy context for signal
generation... temporarily... provided it is explicitly flagged as temporary, symbol mapping is
exact, price divergence is checked before execution, SL/TP are recalculated/validated against
actual cTrader prices/specifications immediately before order submission"). Final architecture
should replace the reference price/SL/TP source with native cTrader candles; this bridge is the
minimum safe vertical slice, not the destination.

Two independent, reversible gates, both required (mirrors CTraderAdapter's own two-gate design):
  1. CTRADER_BENSIM_ENGINE_ENABLED (default False) -- should the bridge even attempt this signal.
  2. CTRADER_ORDER_SUBMISSION_ENABLED (default False, backend/brokers/ctrader/config.py) -- can
     the adapter place a real order once this bridge decides to. A trading-scope OAuth token is
     also required regardless of both flags (cTrader's own server-side enforcement).
Every failure mode (price divergence, risk rejection, broker error, missing credentials) is
caught and logged -- this function must NEVER raise into the caller's MT5 cycle.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

CTRADER_BRIDGE_ACCOUNT_ID = "ctrader_demo"
# How far the cTrader quote may diverge from the MT5-derived reference price before this bridge
# refuses to trade the temporary, cross-broker-priced signal -- expressed as a fraction of the
# original stop distance (e.g. 0.15 = divergence must be under 15% of the planned risk distance).
_DEFAULT_PRICE_DIVERGENCE_TOLERANCE = 0.15


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"false", "0", "off", "no"}


def bridge_enabled() -> bool:
    return _env_flag("CTRADER_BENSIM_ENGINE_ENABLED", False)


# 2026-08-27 real bug found via a live test: CTraderTransport's Twisted reactor is a process-wide
# singleton that CANNOT be restarted once stopped (twisted.internet.error.ReactorNotRestartable,
# confirmed live -- transport.py's own docstring already says "only ONE CTraderTransport should
# ever be constructed per process", which this bridge's first draft violated by creating a fresh
# CTraderAdapter and disconnecting it on every single call). Since this bridge is invoked from
# the LIVE, long-running MT5 cycle every ~5 minutes, that would have connected successfully
# exactly once and then failed on every subsequent cycle for the rest of the process's life.
# Fixed: one adapter, created lazily, reused for the process's lifetime, reconnected only if
# genuinely disconnected -- never torn down after a single attempt.
_bridge_adapter: Any | None = None


async def _get_bridge_adapter(cfg: Any) -> Any:
    global _bridge_adapter
    from backend.brokers.ctrader.adapter import CTraderAdapter
    from backend.brokers.models import BrokerConnectionState

    if _bridge_adapter is None:
        _bridge_adapter = CTraderAdapter(cfg)
    health = await _bridge_adapter.health()
    if health.connection_state != BrokerConnectionState.CONNECTED:
        await _bridge_adapter.connect()
    return _bridge_adapter


@dataclass(frozen=True)
class CTraderBridgeResult:
    status: str  # "DISABLED" | "SKIPPED_<reason>" | "REJECTED_<reason>" | "SUBMITTED" | "ERROR"
    detail: dict[str, Any]


async def attempt_ctrader_bridge_trade(candidate: dict[str, Any], *, confidence: float, dry_run: bool = False) -> CTraderBridgeResult:
    """`candidate` is the SAME naturally-qualified `best` dict MT5's own run_cycle already
    finalized a submission decision for -- this function only ever runs AFTER that, using it as
    a read-only reference, never altering it or MT5's own result."""
    if not bridge_enabled():
        return CTraderBridgeResult(status="DISABLED", detail={})

    try:
        from backend.brokers.ctrader.config import ctrader_config
        from backend.brokers.ctrader.risk import size_ctrader_position
        from backend.brokers.models import BrokerOrderCommand, BrokerOrderIntent
        from backend.mt5_strategies.models import normalize_strategy_id

        cfg = ctrader_config()
        if not (cfg.has_app_credentials and cfg.has_account_tokens and cfg.account_id):
            return CTraderBridgeResult(status="SKIPPED_NO_CREDENTIALS", detail={})

        symbol = str(candidate["canonical_pair"]).upper()
        instrument_id = f"FX:{symbol}"
        direction = str(candidate["direction"])
        reference_price = Decimal(str(candidate.get("context", {}).get("bid") or candidate.get("context", {}).get("ask") or 0)) or None
        sl = Decimal(str(candidate["stop_loss"]))
        tp = Decimal(str(candidate["take_profit"]))
        strategy_id = normalize_strategy_id((candidate.get("context") or {}).get("strategy_id"))
        candidate_id = str(candidate.get("candidate_id") or candidate.get("context_hash") or "UNKNOWN")

        adapter = await _get_bridge_adapter(cfg)
        health = await adapter.health()
        if health.account_verification_status != "VERIFIED_DEMO":
            return CTraderBridgeResult(status="SKIPPED_NOT_VERIFIED_DEMO", detail={"account_verification_status": health.account_verification_status})

        # Immediately-before-submission cross-broker checks (user-mandated, non-negotiable):
        # real cTrader price, symbol resolution, divergence check, SL/TP re-validation against
        # cTrader's own spec -- never silently execute MT5-priced geometry on cTrader.
        quote = await adapter.quote(instrument_id)
        ctrader_price = quote.ask if direction == "LONG" else quote.bid
        if ctrader_price is None:
            return CTraderBridgeResult(status="REJECTED_NO_CTRADER_QUOTE", detail={})

        stop_distance = abs((reference_price or ctrader_price) - sl)
        divergence = abs(ctrader_price - (reference_price or ctrader_price))
        tolerance = Decimal(str(_env_flag_float("CTRADER_BRIDGE_PRICE_DIVERGENCE_TOLERANCE", _DEFAULT_PRICE_DIVERGENCE_TOLERANCE))) * stop_distance
        if stop_distance > 0 and divergence > tolerance:
            return CTraderBridgeResult(
                status="REJECTED_PRICE_DIVERGENCE",
                detail={"mt5_reference_price": str(reference_price), "ctrader_price": str(ctrader_price), "divergence": str(divergence), "tolerance": str(tolerance)},
            )

        spec = await adapter.symbol_spec(instrument_id)
        account_snapshot = await adapter.account_snapshot(cfg.account_id)

        # Same dollar-risk decision MT5's own tier-capped BrokerOrderIntent already carries --
        # NOT re-derived independently. The caller passes the candidate's own tier/confidence
        # context; this bridge re-scales it against cTrader's OWN account equity (a $50k demo
        # account vs. whatever MT5 account produced the signal) using the exact same tier
        # multiplier table, so cTrader's risk is proportional to ITS OWN equity, never MT5's.
        from backend.brokers.mt5.autonomous import _STRATEGY_RISK_TIER, _TIER_RISK_MULTIPLIER, _env_float
        risk_percent = _env_float("MT5_RISK_PERCENT_PER_TRADE", 1.0)
        tier = _STRATEGY_RISK_TIER.get(strategy_id, "A")
        tier_multiplier = _TIER_RISK_MULTIPLIER.get(tier, 1.0)
        base_risk_usd = (account_snapshot.net_liquidation * Decimal(str(risk_percent)) / Decimal("100")).quantize(Decimal("0.01"))
        risk_usd = (base_risk_usd * Decimal(str(tier_multiplier))).quantize(Decimal("0.01"))

        # BrokerAccountSnapshot has no currency field; this account's deposit currency is
        # verified live (via terminal_status/account_snapshot's own margin_currency, confirmed
        # USD for this specific demo account) -- not a guess, but also not yet read dynamically.
        # A genuine multi-currency cTrader account would need that fixed here.
        decision = size_ctrader_position(symbol_name=instrument_id, entry=ctrader_price, stop=sl, risk_usd=risk_usd, spec=spec, account_currency="USD")
        order_intent = BrokerOrderIntent(
            candidate_id=candidate_id, broker="ctrader", account_id=cfg.account_id, strategy_id=strategy_id,
            symbol=symbol, direction=direction, confidence=confidence, risk_tier=tier, risk_usd=risk_usd,
            reference_price=ctrader_price, sl=sl, tp=tp,
        )
        if decision.status != "APPROVED":
            return CTraderBridgeResult(status=f"REJECTED_{decision.reasons[0] if decision.reasons else 'RISK'}", detail={"order_intent": order_intent.model_dump(mode="json"), "decision": decision.__dict__})

        if dry_run or not cfg.order_submission_enabled:
            return CTraderBridgeResult(status="DRY_RUN_OK", detail={"order_intent": order_intent.model_dump(mode="json"), "volume": str(decision.volume), "ctrader_price": str(ctrader_price)})

        command = BrokerOrderCommand(
            canonical_order_id=f"CTRADER_BRIDGE_{candidate_id}", account_id=cfg.account_id, instrument_id=instrument_id,
            side=direction, order_type="MARKET", time_in_force="IOC", quantity=decision.volume, approved_quantity=decision.volume,
            stop_loss=sl, take_profit=tp, risk_evaluation_id=candidate_id, idempotency_key=f"CTRADER_BRIDGE_{candidate_id}",
        )
        receipt = await adapter.submit_order(command)
        # Deliberately NOT disconnecting -- see _get_bridge_adapter's own comment: the Twisted
        # reactor cannot be restarted once stopped, so this connection is kept alive for the
        # process's lifetime and reused on the next cycle, exactly like MT5's own adapter.
        logger.warning(
            "cTrader Bensim bridge trade submitted strategy=%s symbol=%s direction=%s volume=%s risk_usd=%s candidate_id=%s",
            strategy_id, symbol, direction, decision.volume, risk_usd, candidate_id,
        )
        return CTraderBridgeResult(status="SUBMITTED", detail={"order_intent": order_intent.model_dump(mode="json"), "receipt": receipt.model_dump(mode="json")})
    except Exception as exc:
        logger.exception("cTrader Bensim bridge failed (MT5 unaffected): %s", exc.__class__.__name__)
        return CTraderBridgeResult(status="ERROR", detail={"error": exc.__class__.__name__, "message": str(exc)})


def _env_flag_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
