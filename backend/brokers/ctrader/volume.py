"""cTrader raw-volume <-> canonical-lots conversion -- kept as a small, pure, independently-tested
module because a mistake here is exactly the kind of error that silently corrupts position sizing
once execution is ever wired up (Phase 4+, not this phase).

SOURCE OF TRUTH (verified directly against the official proto definitions, spotware/openapi-
proto-messages, OpenApiModelMessages.proto -- not inferred/guessed):

  ProtoOASymbol.lotSize    "Lot size of the Symbol (in cents)."
  ProtoOASymbol.minVolume  "Minimum allowed volume in cents for an order with a symbol."
  ProtoOASymbol.maxVolume  "Maximum allowed volume in cents for an order with a symbol."
  ProtoOASymbol.stepVolume "Step of the volume in cents for an order."

"in cents" means these integers are all expressed in the SAME basis: raw = units * 100. Since
lotSize_raw = units_per_lot * 100, and any other raw volume field = (lots * units_per_lot) * 100
= lots * lotSize_raw, the general conversion is simply:

    lots = raw_volume / lotSize_raw
    raw_volume = lots * lotSize_raw

This holds regardless of the symbol's own units-per-lot convention (100,000 for most FX pairs,
different for CFDs/metals/indices) -- NEVER assume a fixed divisor (e.g. "always /100" or "always
/10,000,000") without lotSize_raw's own value for that specific symbol. A forum thread
(community.ctrader.com/forum/connect-api-support/43672) shows a real user tripped by exactly this
assumption; this module exists specifically so Bensim never repeats it.

ProtoOATrader.moneyDigits: "Specifies the exponent of the monetary values. E.g. moneyDigits = 8
must be interpreted as business value multiplied by 10^8" -- account balance/equity/margin raw
integers use this SEPARATE convention (10^-moneyDigits scaling), unrelated to volume-in-cents.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from backend.brokers.ctrader.exceptions import CTraderUnavailableError


def raw_volume_to_lots(raw_volume: int, lot_size_raw: int) -> Decimal:
    if lot_size_raw <= 0:
        raise CTraderUnavailableError(f"cannot convert cTrader volume: symbol reported lotSize={lot_size_raw} (must be > 0)")
    try:
        return Decimal(raw_volume) / Decimal(lot_size_raw)
    except InvalidOperation as exc:
        raise CTraderUnavailableError(f"cannot convert cTrader volume {raw_volume}/{lot_size_raw}: {exc}") from exc


def lots_to_raw_volume(lots: Decimal, lot_size_raw: int) -> int:
    """Rounds to the nearest whole raw unit -- callers that need step/min/max validation must do
    so separately (this function only performs the unit conversion, never volume validation)."""
    if lot_size_raw <= 0:
        raise CTraderUnavailableError(f"cannot convert lots to cTrader volume: symbol reported lotSize={lot_size_raw} (must be > 0)")
    return int((lots * Decimal(lot_size_raw)).to_integral_value())


def money_from_raw(raw_value: int, money_digits: int) -> Decimal:
    """ProtoOATrader monetary fields (balance/equity/margin/...) are fixed-point integers scaled
    by 10^moneyDigits -- e.g. moneyDigits=8, raw=1000000000000 -> Decimal("10000.00000000")."""
    if money_digits < 0:
        raise CTraderUnavailableError(f"cannot convert cTrader monetary value: invalid moneyDigits={money_digits}")
    return Decimal(raw_value) / (Decimal(10) ** money_digits)


def pip_size_from_position(pip_position: int) -> Decimal:
    if pip_position < 0:
        raise CTraderUnavailableError(f"cannot derive pip size: invalid pipPosition={pip_position}")
    return Decimal(1) / (Decimal(10) ** pip_position)


def point_size_from_digits(digits: int) -> Decimal:
    if digits < 0:
        raise CTraderUnavailableError(f"cannot derive point size: invalid digits={digits}")
    return Decimal(1) / (Decimal(10) ** digits)
