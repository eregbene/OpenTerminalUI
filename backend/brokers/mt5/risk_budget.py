from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

MIN_MULTIPLIER = 0.25
MAX_MULTIPLIER = 1.0

_ECONOMIC_RISK_FACTORS = {"none": 1.0, "low": 0.9, "medium": 0.7, "high": 0.4}


@dataclass(frozen=True)
class RiskBudgetAdjustment:
    multiplier: float
    reasons: list[str]
    components: dict[str, float]


def compute_risk_multiplier(
    *,
    drawdown_pct: float = 0.0,
    portfolio_exposure_ratio: float = 0.0,
    correlated_exposure_ratio: float = 0.0,
    economic_risk_level: str = "none",
    strategy_confidence: float | None = None,
    min_multiplier: float = MIN_MULTIPLIER,
    max_multiplier: float = MAX_MULTIPLIER,
) -> RiskBudgetAdjustment:
    """Effective-risk-budget scaling layer for position sizing. Every factor here is a
    multiplicative TAPER on the account's configured per-trade risk percent (never an increase
    above 1.0x, and never below min_multiplier) -- this is the "effective_risk_budget" the task
    describes: risk_budget adjusted by drawdown/portfolio exposure/correlation/economic
    risk/strategy confidence, all within configured bounds. Deliberately has no path that scales
    UP after a loss (no martingale) and no path driven by anything other than current, live
    account/portfolio state."""
    components: dict[str, float] = {}
    reasons: list[str] = []
    multiplier = 1.0

    if drawdown_pct > 0:
        drawdown_factor = max(0.5, 1.0 - (drawdown_pct / 100.0) * 2.0)
        components["drawdown_factor"] = drawdown_factor
        multiplier *= drawdown_factor
        if drawdown_factor < 1.0:
            reasons.append(f"drawdown_{drawdown_pct:.2f}pct_reduces_risk")

    if portfolio_exposure_ratio > 0.5:
        exposure_factor = max(0.5, 1.0 - (portfolio_exposure_ratio - 0.5))
        components["portfolio_exposure_factor"] = exposure_factor
        multiplier *= exposure_factor
        reasons.append("elevated_portfolio_open_risk_reduces_size")

    if correlated_exposure_ratio > 0.5:
        correlation_factor = max(0.5, 1.0 - (correlated_exposure_ratio - 0.5) * 0.5)
        components["correlation_factor"] = correlation_factor
        multiplier *= correlation_factor
        reasons.append("correlated_symbol_exposure_reduces_size")

    economic_factor = _ECONOMIC_RISK_FACTORS.get(economic_risk_level, 1.0)
    components["economic_risk_factor"] = economic_factor
    multiplier *= economic_factor
    if economic_factor < 1.0:
        reasons.append(f"economic_risk_{economic_risk_level}_reduces_size")

    if strategy_confidence is not None:
        # Confidence scales WITHIN configured bounds only -- 0 confidence still floors at 0.7x,
        # never below min_multiplier overall; full confidence never exceeds 1.0x.
        confidence_factor = max(0.7, min(1.0, 0.7 + max(0.0, min(1.0, strategy_confidence)) * 0.3))
        components["strategy_confidence_factor"] = confidence_factor
        multiplier *= confidence_factor

    multiplier = max(min_multiplier, min(max_multiplier, multiplier))
    if not reasons:
        reasons.append("no_adjustment_full_configured_risk")
    return RiskBudgetAdjustment(multiplier=multiplier, reasons=reasons, components=components)


def effective_risk_budget_usd(
    base_risk_budget_usd: Decimal,
    *,
    drawdown_pct: float = 0.0,
    portfolio_exposure_ratio: float = 0.0,
    correlated_exposure_ratio: float = 0.0,
    economic_risk_level: str = "none",
    strategy_confidence: float | None = None,
) -> tuple[Decimal, dict[str, Any]]:
    adjustment = compute_risk_multiplier(
        drawdown_pct=drawdown_pct,
        portfolio_exposure_ratio=portfolio_exposure_ratio,
        correlated_exposure_ratio=correlated_exposure_ratio,
        economic_risk_level=economic_risk_level,
        strategy_confidence=strategy_confidence,
    )
    adjusted = (base_risk_budget_usd * Decimal(str(adjustment.multiplier))).quantize(Decimal("0.01"))
    return adjusted, {"multiplier": adjustment.multiplier, "reasons": adjustment.reasons, "components": adjustment.components, "base_risk_budget_usd": str(base_risk_budget_usd), "adjusted_risk_budget_usd": str(adjusted)}
