from __future__ import annotations

from decimal import Decimal
from typing import Any

from backend.brokers.mt5.models import MT5Symbol


DEFAULT_SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD")


def symbol_from_raw(raw: Any) -> MT5Symbol:
    data = raw._asdict() if hasattr(raw, "_asdict") else dict(raw)
    return MT5Symbol(
        symbol=str(data.get("name") or data.get("symbol") or ""),
        visible=bool(data.get("visible", False)),
        selected=bool(data.get("select", data.get("selected", False))),
        bid=_dec_or_none(data.get("bid")),
        ask=_dec_or_none(data.get("ask")),
        spread=data.get("spread"),
        digits=data.get("digits"),
        trade_mode=data.get("trade_mode"),
        currency_base=data.get("currency_base"),
        currency_profit=data.get("currency_profit"),
        currency_margin=data.get("currency_margin"),
        path=data.get("path"),
        description=data.get("description"),
        trade_calc_mode=data.get("trade_calc_mode"),
        point=_dec_or_none(data.get("point")),
        trade_tick_size=_dec_or_none(data.get("trade_tick_size")),
        trade_tick_value=_dec_or_none(data.get("trade_tick_value")),
        trade_tick_value_profit=_dec_or_none(data.get("trade_tick_value_profit")),
        trade_tick_value_loss=_dec_or_none(data.get("trade_tick_value_loss")),
        trade_contract_size=_dec_or_none(data.get("trade_contract_size")),
        volume_min=_dec_or_none(data.get("volume_min")),
        volume_max=_dec_or_none(data.get("volume_max")),
        volume_step=_dec_or_none(data.get("volume_step")),
        trade_stops_level=data.get("trade_stops_level"),
        trade_freeze_level=data.get("trade_freeze_level"),
        spread_float=data.get("spread_float"),
        swap_long=_dec_or_none(data.get("swap_long")),
        swap_short=_dec_or_none(data.get("swap_short")),
        swap_mode=data.get("swap_mode"),
        margin_initial=_dec_or_none(data.get("margin_initial")),
        margin_maintenance=_dec_or_none(data.get("margin_maintenance")),
        filling_mode=data.get("filling_mode"),
        order_mode=data.get("order_mode"),
        trade_execution=data.get("trade_execution"),
    )


def _dec_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))
