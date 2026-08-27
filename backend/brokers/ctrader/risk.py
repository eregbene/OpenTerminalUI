"""cTrader-side canonical risk-to-volume conversion (2026-08-27) -- the broker-specific half of
Gate C/D's "same Bensim engine, different execution boundary" design: MT5's own risk pipeline
(backend/brokers/mt5/execution.py::calculate_risk_size) decides a dollar figure (risk_usd,
already confidence-tapered, portfolio-adjusted, and strategy-tier-capped -- see
BrokerOrderIntent's own docstring); THIS module converts that SAME dollar figure into a real
cTrader lot size, using cTrader's own symbol spec, never MT5's.

Deliberately conservative, matching backend/brokers/mt5/risk_calculator.py's own "never guess a
cross-currency conversion rate" posture: loss-per-lot is only computed with confidence when the
symbol's quote currency equals the account's currency (e.g. EURUSD/USD on a USD account) -- a
straight multiply of stop-distance x contract_size. Anything else (a genuine cross, e.g. EURJPY
on a USD account) raises rather than silently mis-sizing; a real conversion-rate lookup is a
later, explicit addition, not a guess baked in here.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from backend.brokers.ctrader.exceptions import CTraderCapabilityError, CTraderUnavailableError
from backend.brokers.models import BrokerSymbolSpec


@dataclass(frozen=True)
class CTraderVolumeDecision:
    status: str  # "APPROVED" | "REJECTED"
    volume: Decimal  # canonical lots, 0 if REJECTED
    loss_per_lot_usd: Decimal | None
    projected_loss_usd: Decimal | None
    reasons: list[str]


def _quote_currency(symbol_name: str) -> str:
    name = symbol_name.upper().replace("FX:", "").replace("CTRADER_SYMBOL_", "")
    return name[3:6] if len(name) >= 6 else name


def loss_per_lot_usd(*, symbol_name: str, entry: Decimal, stop: Decimal, spec: BrokerSymbolSpec, account_currency: str = "USD") -> Decimal:
    """Dollar loss for exactly 1.0 canonical lot, given the stop distance -- the cTrader-native
    equivalent of MT5's calculate_canonical_loss_per_lot, scoped to the one case that needs no
    cross-currency conversion rate. contract_size is already in canonical "units per lot" terms
    (backend/brokers/ctrader/symbols.py::ctrader_symbol_to_spec), so for a symbol whose quote
    currency matches the account currency, dollar loss per lot = price_distance * contract_size
    exactly (1 unit of quote-currency price movement x that many units = that many quote-currency
    dollars, and quote currency == account currency here by construction)."""
    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        raise CTraderUnavailableError(f"cannot compute loss_per_lot: entry and stop are equal or invalid (entry={entry}, stop={stop})")
    quote_ccy = _quote_currency(symbol_name)
    if quote_ccy != account_currency.upper():
        raise CTraderUnavailableError(
            f"cannot compute loss_per_lot for {symbol_name} without a cross-currency conversion rate "
            f"(quote currency {quote_ccy} != account currency {account_currency}) -- refusing to guess, not yet implemented"
        )
    return (stop_distance * spec.contract_size).quantize(Decimal("0.01"))


def size_ctrader_position(
    *, symbol_name: str, entry: Decimal, stop: Decimal, risk_usd: Decimal, spec: BrokerSymbolSpec, account_currency: str = "USD",
) -> CTraderVolumeDecision:
    """Converts an already-decided, already-tier-capped risk_usd into a real cTrader lot size.
    Floors to volume_step and NEVER rounds up to volume_min -- same invariant the strategy-tier
    hard-cap fix depends on and CTraderAdapter._normalize_volume already enforces at the
    execution boundary; this is the SIZING half of that same guarantee, applied before an
    intent is even built. If even volume_min's dollar risk would exceed risk_usd, REJECT."""
    reasons: list[str] = []
    if risk_usd <= 0:
        return CTraderVolumeDecision(status="REJECTED", volume=Decimal("0"), loss_per_lot_usd=None, projected_loss_usd=None, reasons=["INVALID_RISK_USD"])
    try:
        loss_per_lot = loss_per_lot_usd(symbol_name=symbol_name, entry=entry, stop=stop, spec=spec, account_currency=account_currency)
    except CTraderUnavailableError as exc:
        return CTraderVolumeDecision(status="REJECTED", volume=Decimal("0"), loss_per_lot_usd=None, projected_loss_usd=None, reasons=[f"LOSS_PER_LOT_UNAVAILABLE:{exc}"])
    if loss_per_lot <= 0:
        return CTraderVolumeDecision(status="REJECTED", volume=Decimal("0"), loss_per_lot_usd=None, projected_loss_usd=None, reasons=["LOSS_PER_LOT_UNAVAILABLE"])

    raw_lots = risk_usd / loss_per_lot
    step = spec.volume_step
    floored_lots = (raw_lots / step).to_integral_value(rounding=ROUND_FLOOR) * step
    if floored_lots < spec.volume_min:
        minimum_risk = (spec.volume_min * loss_per_lot).quantize(Decimal("0.01"))
        return CTraderVolumeDecision(
            status="REJECTED", volume=Decimal("0"), loss_per_lot_usd=loss_per_lot, projected_loss_usd=minimum_risk,
            reasons=[f"VOLUME_BELOW_MINIMUM_RISK_TOO_HIGH:minVolume={spec.volume_min}_lots_would_risk_${minimum_risk}_vs_approved_${risk_usd}"],
        )
    final_lots = min(floored_lots, spec.volume_max)
    projected_loss = (final_lots * loss_per_lot).quantize(Decimal("0.01"))
    return CTraderVolumeDecision(status="APPROVED", volume=final_lots, loss_per_lot_usd=loss_per_lot, projected_loss_usd=projected_loss, reasons=reasons)
