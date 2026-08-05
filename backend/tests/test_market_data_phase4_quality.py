from __future__ import annotations

from datetime import timedelta

from backend.market_data.fixtures import historical_candles
from backend.market_data.models import QualityFlag
from backend.market_data.validation import CandleValidationPolicy, validate_candles


def test_candle_validation_flags_duplicate_missing_and_invalid_ohlc() -> None:
    bars = historical_candles(duplicate=True, missing=True)
    bars[0].high = bars[0].low
    bars[0].low = bars[0].close + 10
    result = validate_candles(bars, policy=CandleValidationPolicy(expected_interval=timedelta(minutes=1)))
    assert QualityFlag.INVALID_OHLC in result.all_flags
    assert QualityFlag.DUPLICATE_BAR in result.all_flags
    assert QualityFlag.MISSING_BAR in result.all_flags
    assert bars[0].quality.quality_flags


def test_candle_validation_marks_incomplete_and_zero_volume() -> None:
    bars = historical_candles()
    bars[-1].is_complete = False
    bars[-1].volume = 0
    result = validate_candles(bars, policy=CandleValidationPolicy(allow_zero_volume=False))
    assert QualityFlag.INCOMPLETE_BAR in result.all_flags
    assert QualityFlag.ZERO_VOLUME in result.all_flags
