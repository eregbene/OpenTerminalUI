"""Tests for backend/market_structure/liquidity.py::detect_equal_levels -- the EQH/EQL
liquidity-pool clustering added after the 2026-08-17 audit confirmed it was genuinely missing
(config.equal_levels.minimum_touches was declared but had zero call sites). Uses the "balanced"
profile (the engine's own default, MarketStructureEngine.__init__) deliberately -- its base
SwingConfig has no minimum_price_movement/minimum_atr novelty filter, so two swings that are
close in PRICE but well separated in TIME both register as independent swings without needing to
hand-tune ATR-relative thresholds."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.market_structure import MarketStructureEngine, get_profile
from backend.market_structure.bar_utils import normalize_bars


def _bars(values: list[tuple[float, float, float, float]]) -> list[dict[str, object]]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        {
            "timestamp": (start + timedelta(minutes=idx * 15)).isoformat(),
            "open": o, "high": h, "low": l, "close": c,
            "volume": 1000, "is_complete": True,
        }
        for idx, (o, h, l, c) in enumerate(values)
    ]


def _two_equal_highs_fixture() -> list[dict[str, object]]:
    return _bars([
        (100, 101, 99, 100),    # 0
        (100, 102, 100, 101),   # 1
        (101, 103, 100, 102),   # 2
        (102, 105, 101, 104),   # 3
        (104, 110, 103, 108),   # 4  PEAK A high=110
        (108, 107, 104, 105),   # 5
        (105, 106, 103, 104),   # 6
        (104, 105, 102, 103),   # 7
        (103, 102, 99, 100),    # 8
        (100, 101, 97, 98),     # 9  trough low=97 (incidental, not asserted on)
        (98, 100, 97.5, 99),    # 10
        (99, 102, 98, 101),     # 11
        (101, 104, 100, 103),   # 12
        (103, 107, 102, 106),   # 13
        (106, 110.05, 105, 109),  # 14  PEAK B high=110.05 -- within EQH tolerance of peak A
        (109, 108, 105, 106),   # 15
        (106, 107, 104, 105),   # 16
        (105, 106, 103, 104),   # 17
        (104, 103, 100, 101),   # 18
        (101, 102, 99, 100),    # 19
        (100, 105, 99, 103),    # 20
        (103, 108, 102, 106),   # 21
        (106, 113, 105, 108),   # 22  breaches equal-high level (113 > ~110) and reclaims (close 108 < level)
        (108, 109, 105, 107),   # 23
        (107, 108, 103, 105),   # 24
    ])


def _analyze():
    bars = normalize_bars(_two_equal_highs_fixture(), symbol="TEST", timeframe="15m")
    return MarketStructureEngine(get_profile("balanced")).analyze(bars, symbol="TEST", timeframe="15m")


def test_two_near_equal_highs_form_an_eqh_pool():
    snapshot = _analyze()
    assert snapshot.equal_levels, "expected at least one EQH/EQL pool from the two near-equal peaks"
    eqh = [lvl for lvl in snapshot.equal_levels if lvl.side == "buy_side"]
    assert eqh, snapshot.equal_levels
    pool = eqh[0]
    assert pool.touch_count == 2
    assert pool.source == "equal_level"
    assert 109.9 < float(pool.level) < 110.2  # running average of 110 and 110.05


def test_eqh_pool_not_emitted_before_second_touch():
    """No-lookahead: a single swing high must never itself be reported as an equal-level pool --
    detect_equal_levels only emits once minimum_touches (2) is reached."""
    single_peak = _bars([
        (100, 101, 99, 100), (100, 102, 100, 101), (101, 103, 100, 102),
        (102, 105, 101, 104), (104, 110, 103, 108), (108, 107, 104, 105),
        (105, 106, 103, 104), (104, 105, 102, 103), (103, 102, 99, 100),
    ])
    bars = normalize_bars(single_peak, symbol="TEST", timeframe="15m")
    snapshot = MarketStructureEngine(get_profile("balanced")).analyze(bars, symbol="TEST", timeframe="15m")
    assert snapshot.swings  # the lone peak still registers as an ordinary swing
    assert not snapshot.equal_levels  # but never as an EQH pool with only one touch


def test_eqh_pool_confirmation_time_is_the_confirming_touch_not_the_first():
    snapshot = _analyze()
    pool = [lvl for lvl in snapshot.equal_levels if lvl.side == "buy_side"][0]
    first_touch_swing = min((s for s in snapshot.swings if s.swing_type == "high"), key=lambda s: s.bar_index)
    assert pool.confirmation_time > first_touch_swing.confirmation_time


def test_eqh_pool_gets_swept_and_reclaimed():
    snapshot = _analyze()
    assert snapshot.equal_level_sweeps, "expected the later breakout-then-reclaim bar to sweep the EQH pool"
    sweep = snapshot.equal_level_sweeps[0]
    assert sweep.side == "buy_side"
    assert float(sweep.swept_price) > 110.0
    assert float(sweep.reclaim_price) < float(sweep.swept_price)


def test_equal_levels_does_not_change_existing_liquidity_fields():
    """Purely additive: liquidity_levels/liquidity_sweeps (used by every existing strategy/HI
    consumer) must contain exactly what they did before this feature existed -- one entry per
    swing, never merged/deduplicated by the new clustering pass."""
    snapshot = _analyze()
    high_swings = [s for s in snapshot.swings if s.swing_type == "high"]
    high_levels = [lvl for lvl in snapshot.liquidity_levels if lvl.side == "buy_side"]
    assert len(high_levels) == len(high_swings)
    assert all(lvl.source == "confirmed_swing" and lvl.touch_count == 1 for lvl in high_levels)


def test_equal_level_config_minimum_touches_is_respected():
    config = get_profile("balanced")
    config.equal_levels.minimum_touches = 3
    bars = normalize_bars(_two_equal_highs_fixture(), symbol="TEST", timeframe="15m")
    snapshot = MarketStructureEngine(config).analyze(bars, symbol="TEST", timeframe="15m")
    # Only two touches exist in the fixture -- raising the bar to 3 must suppress the pool entirely.
    assert not [lvl for lvl in snapshot.equal_levels if lvl.side == "buy_side"]
