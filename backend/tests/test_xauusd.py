from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.forex_frameworks.service import context_from_snapshot, framework_service
from backend.forex_intelligence.instruments import get_forex_instrument, provider_symbol
from backend.forex_intelligence.service import ForexIntelligenceService


def _gold_bars(count: int = 96) -> list[dict[str, object]]:
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    price = 2350.0
    rows = []
    for idx in range(count):
        close = price + 0.8 + ((idx % 8) - 4) * 0.15
        rows.append({"timestamp": (start + timedelta(hours=idx)).isoformat(), "open": price, "high": max(price, close) + 2.0, "low": min(price, close) - 2.0, "close": close, "volume": 1500 + idx * 2, "is_complete": True})
        price = close
    return rows


def test_xauusd_registry_uses_gold_specific_proxy_metadata() -> None:
    instrument = get_forex_instrument("XAUUSD")

    assert instrument.asset_type == "commodity"
    assert instrument.instrument_class == "precious_metal"
    assert instrument.price_precision == 2
    assert str(instrument.pip_size) == "0.10"
    assert str(instrument.point_size) == "1.00"
    assert instrument.is_proxy is True
    assert provider_symbol("XAUUSD", "yahoo") == "GC=F"


def test_xauusd_intelligence_and_framework_context() -> None:
    bars = _gold_bars()
    snapshot = ForexIntelligenceService().analyze_rows(bars, symbol="XAUUSD", timeframe="1h", provider_symbol_value="GC=F", source_provider="yahoo").model_dump(mode="json")
    candles = [{"t": int(datetime.fromisoformat(row["timestamp"]).timestamp()), "o": row["open"], "h": row["high"], "l": row["low"], "c": row["close"], "v": row["volume"]} for row in bars]
    ctx = context_from_snapshot(snapshot, candles)
    signals = framework_service.analyze(ctx, ["trend_following", "ict", "vsa"])

    assert snapshot["symbol"] == "XAUUSD"
    assert snapshot["provider_symbol"] == "GC=F"
    assert ctx.provider_metadata["is_proxy"] is True
    assert {signal.framework_id for signal in signals} == {"trend_following", "ict", "vsa"}
    assert next(signal for signal in signals if signal.framework_id == "vsa").status.value == "INSUFFICIENT_DATA"
