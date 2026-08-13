from __future__ import annotations

import asyncio

from backend.api.routes import stocks


def test_top_tickers_returns_crude_gold_silver_with_no_price_source() -> None:
    # No commodity-futures quote source is wired in (the Yahoo Finance batch-quotes endpoint
    # this used to call was removed, with no replacement) -- items still come back with the
    # right keys/labels/symbols, but price/change_pct degrade to None rather than a fabricated
    # value.
    result = asyncio.run(stocks.get_top_bar_tickers())
    assert len(result.items) == 3
    assert [item.key for item in result.items] == ["crude", "gold", "silver"]
    assert [item.symbol for item in result.items] == ["CL=F", "GC=F", "SI=F"]
    assert all(item.price is None for item in result.items)
    assert all(item.change_pct is None for item in result.items)
