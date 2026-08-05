from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.market_structure import MarketStructureEngine, get_profile
from backend.market_structure.bar_utils import normalize_bars


def _bars(values: list[tuple[float, float, float, float]], *, complete_last: bool = True) -> list[dict[str, object]]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows: list[dict[str, object]] = []
    for idx, (open_, high, low, close) in enumerate(values):
        rows.append(
            {
                "timestamp": (start + timedelta(minutes=idx * 15)).isoformat(),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1000 + idx * 50,
                "is_complete": complete_last or idx < len(values) - 1,
            }
        )
    return rows


def _fixture() -> list[dict[str, object]]:
    return _bars(
        [
            (100, 101, 99, 100),
            (100, 103, 99.5, 102),
            (102, 105, 101, 104),
            (104, 106, 102, 103),
            (103, 104, 100, 101),
            (101, 102, 98, 99),
            (99, 101, 97, 100),
            (100, 104, 99, 103),
            (103, 108, 102, 107),
            (107, 111, 106, 110),
            (110, 112, 107, 108),
            (108, 109, 104, 105),
            (105, 106, 101, 102),
            (102, 104, 100, 103),
            (103, 110, 102, 109),
            (109, 116, 108, 115),
            (115, 118, 112, 117),
            (117, 119, 113, 114),
            (114, 115, 109, 110),
            (110, 121, 109, 120),
        ]
    )


def test_configuration_hash_is_stable() -> None:
    config = get_profile("balanced")
    assert config.configuration_hash() == get_profile("balanced").configuration_hash()
    assert config.configuration_hash() != get_profile("external").configuration_hash()


def test_swing_confirmation_is_delayed_and_no_lookahead() -> None:
    config = get_profile("internal")
    snapshot = MarketStructureEngine(config).analyze(normalize_bars(_fixture(), symbol="TEST", timeframe="15m"), symbol="TEST", timeframe="15m")
    assert snapshot.swings
    first = snapshot.swings[0]
    assert first.confirmation_time is not None
    assert first.confirmation_time > first.candidate_time
    assert first.metadata["confirmation_delay_bars"] == config.swings.right_bars


def test_vertical_slice_detects_core_concepts() -> None:
    snapshot = MarketStructureEngine(get_profile("internal")).analyze(normalize_bars(_fixture(), symbol="TEST", timeframe="15m"), symbol="TEST", timeframe="15m")
    assert snapshot.trend is not None
    assert snapshot.displacements
    assert snapshot.liquidity_levels
    assert snapshot.imbalances
    assert snapshot.dealing_ranges
    assert snapshot.overlays
    assert snapshot.events
    assert snapshot.features
    assert snapshot.score is not None


def test_incomplete_last_bar_is_ignored_by_default() -> None:
    config = get_profile("internal")
    snapshot = MarketStructureEngine(config).analyze(normalize_bars(_fixture() + _bars([(120, 150, 119, 149)], complete_last=False), symbol="TEST", timeframe="15m"), symbol="TEST", timeframe="15m")
    assert snapshot.warnings
    assert "ignored 1 incomplete" in snapshot.warnings[0]


def test_incremental_and_batch_equivalence_for_confirmed_events() -> None:
    config = get_profile("internal")
    engine = MarketStructureEngine(config)
    all_bars = normalize_bars(_fixture(), symbol="TEST", timeframe="15m")
    batch = engine.analyze(all_bars, symbol="TEST", timeframe="15m")
    first = engine.analyze(all_bars[:10], symbol="TEST", timeframe="15m")
    incremental = engine.analyze_incremental(first.state, all_bars[10:], symbol="TEST", timeframe="15m")
    assert [s.id for s in incremental.swings] == [s.id for s in batch.swings]
    assert [b.id for b in incremental.breaks] == [b.id for b in batch.breaks]
