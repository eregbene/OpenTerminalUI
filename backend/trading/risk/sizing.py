from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import Any

from backend.trading.models import InstrumentReference, MarketReference, PaperAccountSnapshot, SizingDecision


def _decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return default


def align_quantity(quantity: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        raise ValueError("quantity step must be positive")
    return (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step


def size_order(
    sizing_intent: dict[str, Any],
    requested_quantity: Decimal | None,
    account: PaperAccountSnapshot,
    instrument: InstrumentReference,
    market: MarketReference,
    invalidation_price: Decimal | None,
) -> SizingDecision:
    errors: list[str] = []
    warnings: list[str] = []
    mode = str(sizing_intent.get("type") or sizing_intent.get("mode") or ("fixed_units" if requested_quantity else "fixed_notional"))
    price = market.price * market.conversion_rate
    if price <= 0:
        errors.append("invalid market price")
        return SizingDecision(requested_quantity=Decimal("0"), approved_quantity=Decimal("0"), requested_notional=Decimal("0"), approved_notional=Decimal("0"), errors=errors)

    quantity = requested_quantity or Decimal("0")
    if mode == "fixed_units":
        quantity = requested_quantity or _decimal(sizing_intent.get("quantity"))
    elif mode == "fixed_notional":
        quantity = _decimal(sizing_intent.get("notional"), Decimal("0")) / (price * instrument.contract_multiplier)
    elif mode == "percent_equity":
        notional = account.equity * (_decimal(sizing_intent.get("percent"), Decimal("0")) / Decimal("100"))
        quantity = notional / (price * instrument.contract_multiplier)
    elif mode == "percent_risk":
        if invalidation_price is None:
            errors.append("missing stop/invalidation for percentage-risk sizing")
        else:
            stop_distance = abs(price - invalidation_price * market.conversion_rate)
            if stop_distance <= 0:
                errors.append("zero stop distance")
            else:
                risk_budget = account.equity * (_decimal(sizing_intent.get("risk_percent"), Decimal("1")) / Decimal("100"))
                quantity = risk_budget / (stop_distance * instrument.contract_multiplier)
    elif mode in {"volatility_target", "equal_weight", "portfolio_weight"}:
        target = _decimal(sizing_intent.get("target_percent") or sizing_intent.get("weight_percent"), Decimal("1"))
        notional = account.equity * (target / Decimal("100"))
        quantity = notional / (price * instrument.contract_multiplier)
        warnings.append(f"{mode} uses simplified deterministic notional sizing")
    else:
        errors.append(f"unsupported sizing mode: {mode}")

    if instrument.lot_size <= 0:
        errors.append("invalid quantity increment")
    quantity = max(Decimal("0"), align_quantity(quantity, instrument.lot_size)) if not errors else Decimal("0")
    notional = quantity * price * instrument.contract_multiplier
    if quantity <= 0 and not errors:
        errors.append("calculated quantity is zero")
    if account.buying_power < notional and not errors:
        errors.append("insufficient buying power")
    return SizingDecision(
        requested_quantity=requested_quantity or quantity,
        approved_quantity=Decimal("0") if errors else quantity,
        requested_notional=(requested_quantity or quantity) * price * instrument.contract_multiplier,
        approved_notional=Decimal("0") if errors else notional,
        warnings=warnings,
        errors=errors,
    )
