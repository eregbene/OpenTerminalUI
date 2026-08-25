"""Bensim -- strategy risk-tier hard-cap fix verification (2026-08-25).

Calls the REAL calculate_risk_size() against REAL live account equity + REAL live symbol_info
for a representative candidate per tier (mtfai1=A, vwap_reversion=B, session_breakout=C) on
EURUSD, across all 4 DEMO accounts, at two confidence levels (60 and 95) to prove confidence can
reduce risk within a tier but never push it above the tier's dollar ceiling.

Read-only except for one live quote/symbol_info fetch per account -- no orders placed.
"""
import asyncio
from decimal import Decimal

from backend.brokers.mt5.multi_account import adapter_for_account
from backend.brokers.mt5.autonomous import MT5AutonomousTradingService
from backend.mt5_strategies import redis_layer

ACCOUNTS = ["demo_10k", "ftmo_demo_25k", "ftmo_demo_50k", "ftmo_demo_100k"]
REPRESENTATIVE = [("mtfai1", "A"), ("vwap_reversion", "B"), ("session_breakout", "C")]
CONFIDENCES = [60.0, 95.0]


async def check_one(account_id: str, strategy_id: str, tier: str, confidence: float):
    service = MT5AutonomousTradingService(adapter_for_account(account_id))
    account = await service.adapter.mt5_account()
    symbol = await redis_layer.cached_symbol_info(service.adapter, "EURUSD")
    quote = await service.adapter.latest_tick("EURUSD")
    entry = quote.ask
    stop_distance = entry * Decimal("0.002")  # ~20 pips on EURUSD, representative M15 stop
    stop = entry - stop_distance
    target = entry + stop_distance * Decimal("2")

    candidate = {"context": {"strategy_id": strategy_id}}
    tier_factor, tier_detail = service._strategy_tier_risk_factor(candidate)
    correlation_factor, correlation_detail = await service._correlation_matrix_risk_factor(candidate)
    risk_adjustment = service._risk_budget_adjustment(account, {}, confidence)

    base_risk_budget = (account.equity * Decimal(str(service.config.risk_percent_per_trade)) / Decimal("100")).quantize(Decimal("0.01"))
    from backend.brokers.mt5.risk_budget import effective_risk_budget_usd
    _, risk_adjustment_detail = effective_risk_budget_usd(base_risk_budget, **risk_adjustment)

    risk = await service.execution.calculate_risk_size(
        account_equity=account.equity, symbol=symbol, direction="LONG", entry=entry, stop=stop, target=target,
        risk_budget_adjustment=risk_adjustment_detail, portfolio_available_risk_usd=None, prop_remaining_budget_usd=None,
        strategy_tier_cap_multiplier=tier_factor, account_fingerprint=None, account_currency=account.currency,
    )
    tier_cap_usd = (base_risk_budget * Decimal(str(tier_factor))).quantize(Decimal("0.01"))
    violation = risk.effective_risk_usd > tier_cap_usd + Decimal("0.02")
    return {
        "account": account_id, "strategy": strategy_id, "tier": tier, "confidence": confidence,
        "base_risk_usd": base_risk_budget, "confidence_multiplier": risk.risk_multiplier,
        "tier_cap_multiplier": tier_factor, "tier_cap_usd": tier_cap_usd,
        "final_risk_usd": risk.effective_risk_usd, "final_lot": risk.volume, "status": risk.status,
        "reasons": risk.reasons, "violation": violation,
    }


async def main():
    print(f"{'account':<16}{'strategy':<20}{'tier':<6}{'conf':>6}{'base_$':>10}{'conf_mult':>11}{'tier_cap_mult':>15}{'tier_cap_$':>12}{'final_$':>10}{'final_lot':>11}{'status':>12}")
    any_violation = False
    for account_id in ACCOUNTS:
        for strategy_id, tier in REPRESENTATIVE:
            for confidence in CONFIDENCES:
                r = await check_one(account_id, strategy_id, tier, confidence)
                any_violation = any_violation or r["violation"]
                flag = " <<< TIER CAP VIOLATED" if r["violation"] else ""
                print(f"{r['account']:<16}{r['strategy']:<20}{r['tier']:<6}{r['confidence']:>6.0f}{float(r['base_risk_usd']):>10.2f}{float(r['confidence_multiplier']):>11.3f}{r['tier_cap_multiplier']:>15.3f}{float(r['tier_cap_usd']):>12.2f}{float(r['final_risk_usd']):>10.2f}{float(r['final_lot']):>11.4f}{r['status']:>12}{flag}")
    print(f"\nany tier-cap violation across all {len(ACCOUNTS)*len(REPRESENTATIVE)*len(CONFIDENCES)} checks: {any_violation}")


if __name__ == "__main__":
    asyncio.run(main())
