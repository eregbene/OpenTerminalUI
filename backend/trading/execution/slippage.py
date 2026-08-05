from __future__ import annotations

from decimal import Decimal


def apply_bps(price: Decimal, bps: Decimal, direction: Decimal) -> Decimal:
    return price + price * (bps / Decimal("10000")) * direction
