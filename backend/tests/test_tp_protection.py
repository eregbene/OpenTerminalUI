from __future__ import annotations

import pytest

from backend.adaptive_management import tp_protection


def test_tp_progress_buy_and_sell_directional():
    assert tp_protection.tp_progress("LONG", 1.1000, 1.1080, 1.1100) == pytest.approx(0.8)
    assert tp_protection.tp_progress("SHORT", 1.1000, 1.0920, 1.0900) == pytest.approx(0.8)
    assert tp_protection.tp_progress("LONG", 1.1000, 1.1000, 1.1000) is None


def test_progress_zone_boundaries():
    assert tp_protection.progress_zone(None) == "none"
    assert tp_protection.progress_zone(0.10) == "below_60"
    assert tp_protection.progress_zone(0.60) == "zone_60_75"
    assert tp_protection.progress_zone(0.749) == "zone_60_75"
    assert tp_protection.progress_zone(0.75) == "zone_75_85"
    assert tp_protection.progress_zone(0.85) == "zone_85_95"
    assert tp_protection.progress_zone(0.95) == "zone_95_plus"
    assert tp_protection.progress_zone(1.10) == "zone_95_plus"


def test_retracement_allowance_wider_for_trending_than_ranging():
    trending = tp_protection.retracement_allowance(atr_r=1.0, regime="trending_up", timeframe="M5")
    ranging = tp_protection.retracement_allowance(atr_r=1.0, regime="ranging", timeframe="M5")
    event = tp_protection.retracement_allowance(atr_r=1.0, regime="event_driven", timeframe="M5")
    assert trending > ranging > 0
    assert event <= ranging
    assert 0.15 <= trending <= 0.60


def test_retracement_allowance_respects_bounds():
    value = tp_protection.retracement_allowance(atr_r=10.0, regime="trending_up", timeframe="D1", min_fraction=0.15, max_fraction=0.6)
    assert value <= 0.6


def test_classify_retracement_bands():
    assert tp_protection.classify_retracement(0.1, 1.0) == "normal"
    assert tp_protection.classify_retracement(0.8, 1.0) == "elevated"
    assert tp_protection.classify_retracement(1.2, 1.0) == "abnormal"
    assert tp_protection.classify_retracement(2.0, 1.0) == "thesis_invalidating"


def test_resolve_profit_lock_floor_bands():
    below = tp_protection.resolve_profit_lock_floor(max_tp_progress=0.5, max_achieved_r=2.0, regime="ranging", atr_r=1.0)
    assert below["triggered"] is False

    zone_80 = tp_protection.resolve_profit_lock_floor(max_tp_progress=0.82, max_achieved_r=2.0, regime="ranging", atr_r=1.0)
    assert zone_80["triggered"] is True
    assert 0.50 <= zone_80["protect_fraction"] <= 0.70
    assert zone_80["floor_r"] == pytest.approx(2.0 * zone_80["protect_fraction"])

    zone_90 = tp_protection.resolve_profit_lock_floor(max_tp_progress=0.93, max_achieved_r=2.0, regime="ranging", atr_r=1.0)
    assert zone_90["triggered"] is True
    assert 0.70 <= zone_90["protect_fraction"] <= 0.85
    assert zone_90["protect_fraction"] > zone_80["protect_fraction"]

    trending_80 = tp_protection.resolve_profit_lock_floor(max_tp_progress=0.82, max_achieved_r=2.0, regime="trending_up", atr_r=1.0)
    assert trending_80["protect_fraction"] <= zone_80["protect_fraction"]


def test_classify_winner_preservation_requires_candle_history():
    result = tp_protection.classify_winner_preservation({"opposing_candles": 0, "retracement_state": "normal", "regime": "trending_up", "direction": "LONG"})
    assert result["classification"] == "insufficient_data"


def test_classify_winner_preservation_one_tick_is_not_weakening():
    result = tp_protection.classify_winner_preservation(
        {"opposing_candles": 1, "retracement_state": "elevated", "regime": "trending_up", "direction": "LONG", "candles_held": 10}
    )
    assert result["classification"] in {"healthy_pullback", "strong_continuation"}


def test_classify_winner_preservation_strong_continuation():
    result = tp_protection.classify_winner_preservation(
        {"opposing_candles": 0, "retracement_state": "normal", "regime": "trending_up", "direction": "LONG", "candles_held": 10}
    )
    assert result["classification"] == "strong_continuation"


def test_classify_winner_preservation_weakening_requires_confirmation():
    result = tp_protection.classify_winner_preservation(
        {"opposing_candles": 2, "retracement_state": "abnormal", "regime": "ranging", "direction": "LONG", "candles_held": 10}
    )
    assert result["classification"] == "critical"

    result_weak = tp_protection.classify_winner_preservation(
        {"opposing_candles": 2, "retracement_state": "elevated", "regime": "ranging", "direction": "LONG", "candles_held": 10}
    )
    assert result_weak["classification"] == "weakening"


def test_classify_winner_preservation_invalidated():
    result = tp_protection.classify_winner_preservation(
        {"opposing_candles": 3, "retracement_state": "thesis_invalidating", "regime": "ranging", "direction": "LONG", "candles_held": 10}
    )
    assert result["classification"] == "invalidated"


def test_classify_stop_quality_flags():
    tight = tp_protection.classify_stop_quality(sl_distance=0.0005, atr=0.0010, spread=0.0002, structure_distance=None, broker_min_stop=None)
    assert "stop_too_tight" in tight["flags"] or "stop_inside_noise" in tight["flags"]

    wide = tp_protection.classify_stop_quality(sl_distance=0.0100, atr=0.0010, spread=0.0001, structure_distance=None, broker_min_stop=None)
    assert "stop_too_wide" in wide["flags"]

    acceptable = tp_protection.classify_stop_quality(sl_distance=0.0020, atr=0.0010, spread=0.0001, structure_distance=0.0019, broker_min_stop=None)
    assert acceptable["classification"] == "acceptable"

    insufficient = tp_protection.classify_stop_quality(sl_distance=0.0020, atr=None, spread=0.0001, structure_distance=None, broker_min_stop=None)
    assert insufficient["classification"] == "insufficient_data"


def test_construct_dynamic_stop_long_and_short():
    long_stop = tp_protection.construct_dynamic_stop("LONG", 1.1000, 1.0980, 0.0010, 0.0001, min_atr_mult=1.0, max_atr_mult=3.0)
    assert long_stop is not None
    assert long_stop < 1.1000

    short_stop = tp_protection.construct_dynamic_stop("SHORT", 1.1000, 1.1020, 0.0010, 0.0001, min_atr_mult=1.0, max_atr_mult=3.0)
    assert short_stop is not None
    assert short_stop > 1.1000


def test_construct_dynamic_stop_rejects_when_no_valid_stop():
    assert tp_protection.construct_dynamic_stop("LONG", 1.1000, 1.0999, 0.0010, 0.0001, min_atr_mult=2.0, max_atr_mult=1.0) is None
    assert tp_protection.construct_dynamic_stop("LONG", 1.1000, None, None, 0.0001) is None
    assert tp_protection.construct_dynamic_stop("LONG", 1.1000, 1.0990, 0.0001, 0.0100, min_spread_ratio=5.0) is None
