from __future__ import annotations

from decimal import Decimal


def cap_quantity(requested: Decimal, limit: Decimal | None) -> Decimal:
    return requested if limit is None else min(requested, limit)
