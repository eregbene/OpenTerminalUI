from __future__ import annotations

from decimal import Decimal
from typing import Any

from backend.brokers.mt5.models import MT5Account


def account_from_raw(raw: dict[str, Any]) -> MT5Account:
    return MT5Account(
        login=int(raw.get("login") or 0),
        server=raw.get("server"),
        currency=raw.get("currency"),
        balance=Decimal(str(raw.get("balance") or "0")),
        equity=Decimal(str(raw.get("equity") or "0")),
        margin=Decimal(str(raw.get("margin") or "0")),
        free_margin=Decimal(str(raw.get("margin_free") or raw.get("free_margin") or "0")),
        leverage=int(raw.get("leverage") or 0) if raw.get("leverage") is not None else None,
        name=raw.get("name"),
        company=raw.get("company"),
        trade_mode=raw.get("trade_mode"),
    )
