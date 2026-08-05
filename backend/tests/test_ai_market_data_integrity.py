from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.intelligence.trading.candles import audit_canonical_candles, canonicalize_candles, classify_gap, resample_candles
from backend.intelligence.trading.market_context import build_market_context
from backend.intelligence.trading.strategies import EMATrendStrategy, PullbackStrategy, SupportResistanceBounceStrategy, classify_higher_timeframe


def rows(count: int, *, start: datetime | None = None, drift: float = 0.0001, volume: float = 0) -> list[dict[str, float]]:
    base = int((start or datetime(2026, 7, 27, tzinfo=timezone.utc)).timestamp())
    price = 1.08
    out = []
    for idx in range(count):
        price += drift
        out.append({"t": base + idx * 900, "o": price - drift / 2, "h": price + 0.0003, "l": price - 0.0003, "c": price, "v": volume})
    return out


def test_canonical_candles_reject_invalid_duplicate_and_out_of_order():
    now = datetime(2026, 7, 27, 3, tzinfo=timezone.utc)
    sample = rows(4)
    sample = [sample[1], sample[0], sample[1], {"t": sample[2]["t"], "o": 1.0, "h": 0.9, "l": 1.1, "c": 1.0, "v": 0}]
    candles, meta = canonicalize_candles(sample, symbol="EURUSD", timeframe="15m", source="EURUSD=X", now=now)
    quality = audit_canonical_candles(candles, timeframe="15m", source="EURUSD=X", now=now, canonical_meta=meta)

    assert quality["status"] == "INVALID"
    assert "DUPLICATE_CANDLES" in quality["reasons"]
    assert "OUT_OF_ORDER_CANDLES" in quality["reasons"]
    assert "INVALID_OHLC" in quality["reasons"]
    assert quality["volume_type"] == "UNAVAILABLE"


def test_forex_weekend_gap_is_not_counted_as_provider_defect():
    now = datetime(2026, 7, 27, 3, tzinfo=timezone.utc)
    friday = datetime(2026, 7, 24, 21, 45, tzinfo=timezone.utc)
    sunday = datetime(2026, 7, 26, 22, 0, tzinfo=timezone.utc)
    sample = rows(1, start=friday) + rows(1, start=sunday)
    candles, meta = canonicalize_candles(sample, symbol="EURUSD", timeframe="15m", source="EURUSD=X", now=now)
    quality = audit_canonical_candles(candles, timeframe="15m", source="EURUSD=X", now=now, canonical_meta=meta)

    assert classify_gap(candles[0].close_timestamp, candles[1].open_timestamp) == "WEEKEND"
    assert quality["missing_intervals"] == 0
    assert "MISSING_INTERVALS" not in quality["reasons"]


def test_resampling_requires_complete_parent_candles_and_keeps_parent_refs():
    now = datetime(2026, 7, 27, 5, tzinfo=timezone.utc)
    candles, _ = canonicalize_candles(rows(16), symbol="EURUSD", timeframe="15m", source="IBKR", now=now)
    one_hour, meta_1h = resample_candles(candles[:4], target_timeframe="1h")
    four_hour, meta_4h = resample_candles(candles, target_timeframe="4h")
    incomplete, meta_incomplete = resample_candles(candles[:15], target_timeframe="4h")

    assert len(one_hour) == 1
    assert len(one_hour[0].parent_refs) == 4
    assert one_hour[0].is_synthetic is True
    assert meta_1h["method"] == "4x15m_ohlc"
    assert len(four_hour) == 1
    assert meta_4h["method"] == "16x15m_ohlc"
    assert incomplete == []
    assert meta_incomplete["incomplete_groups"] == 1


def test_zero_yahoo_volume_is_unavailable_not_low_activity():
    candles, meta = canonicalize_candles(rows(8, volume=0), symbol="EURUSD", timeframe="15m", source="EURUSD=X", now=datetime(2026, 7, 27, 3, tzinfo=timezone.utc))
    assert candles
    assert meta["volume_type"] == "UNAVAILABLE"
    assert all("VOLUME_UNAVAILABLE" in candle.quality_flags for candle in candles)


def test_higher_timeframe_unavailable_is_not_conflict():
    assert classify_higher_timeframe("LONG", "unknown") == "UNAVAILABLE"
    assert classify_higher_timeframe("LONG", "neutral") == "NEUTRAL"
    assert classify_higher_timeframe("LONG", "bearish") == "CONFLICT"
    assert classify_higher_timeframe("SHORT", "bearish") == "ALIGNED"


def test_ema_and_pullback_are_reachable_with_valid_warmup():
    trend_ctx = build_market_context(rows(240, drift=0.00008, volume=1000), symbol="EURUSD", timeframe="15m", spread=0.8, higher_timeframe_trend="bullish")
    ema = EMATrendStrategy().evaluate(trend_ctx)
    assert ema.decision == "LONG"
    assert ema.confidence == 0.72

    pullback_rows = rows(240, drift=0.00005, volume=1000)
    last = pullback_rows[-1]
    last["c"] = last["o"] - 0.0001
    last["h"] = max(last["h"], last["o"])
    last["l"] = min(last["l"], last["c"] - 0.0002)
    pullback_ctx = build_market_context(pullback_rows, symbol="EURUSD", timeframe="15m", spread=0.8, higher_timeframe_trend="unknown")
    pullback = PullbackStrategy().evaluate(pullback_ctx)
    assert "HIGHER_TIMEFRAME_CONFLICT" not in (pullback.rejection_codes or [])


def test_support_resistance_bounce_requires_current_rejection_against_prior_level():
    sample = rows(80, drift=0, volume=1000)
    prior_support = sample[-2]["l"]
    sample[-1] = {"t": sample[-1]["t"], "o": prior_support + 0.00008, "h": prior_support + 0.0002, "l": prior_support - 0.00008, "c": prior_support + 0.0001, "v": 1000}
    ctx = build_market_context(sample, symbol="EURUSD", timeframe="15m", spread=0.8)
    signal = SupportResistanceBounceStrategy().evaluate(ctx)

    assert signal.decision == "LONG"
    assert "current candle rejected lower prices" in signal.reason
