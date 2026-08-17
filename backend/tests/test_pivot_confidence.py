"""Tests for backend/market_structure/pivot_confidence.py -- the independently-implemented
kNN pivot-reliability scoring (LuxAlgo kNN Market Architecture's public methodology description,
see docs/EXTERNAL_INDICATOR_REDUNDANCY_AUDIT.md). Split into (1) pure unit tests of the kNN
scoring logic against hand-built PivotSignature fixtures -- including the no-lookahead guarantee,
which is the one property most worth nailing down precisely -- and (2) one end-to-end smoke test
on real generated bars through detect_swings + compute_signatures, confirming the full pipeline
runs and produces sane types."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backend.market_structure.bar_utils import StructureBar, normalize_bars
from backend.market_structure.configuration import get_profile
from backend.market_structure.pivot_confidence import PivotSignature, compute_signatures, pivot_confidence_series
from backend.market_structure.swings import detect_swings

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _sig(swing_id: str, swing_type: str, bar_index: int, atr: float, vol: float, held: bool | None) -> PivotSignature:
    return PivotSignature(swing_id=swing_id, swing_type=swing_type, bar_index=bar_index, relative_atr=atr, relative_volume=vol, held=held)


def test_insufficient_pool_returns_none():
    sigs = [_sig(f"s{i}", "high", i * 30, 1.0, 1.0, True) for i in range(3)]
    query = _sig("query", "high", 500, 1.0, 1.0, None)
    result = pivot_confidence_series(sigs + [query], k=8)
    assert result["query"] is None  # only 3 prior labeled candidates, k=8


def test_confidence_reflects_nearest_neighbor_hold_rate():
    # 8 prior "calm" pivots (low atr/vol ratio) that all HELD, 8 prior "volatile" pivots (high
    # ratio) that all FAILED, spaced far enough apart (30 bars) that hold_horizon_bars=20 has
    # always elapsed well before the next one.
    calm_held = [_sig(f"calm{i}", "high", i * 30, 0.8, 0.9, True) for i in range(8)]
    volatile_failed = [_sig(f"vol{i}", "high", 240 + i * 30, 2.5, 2.2, False) for i in range(8)]
    query_calm = _sig("query_calm", "high", 600, 0.82, 0.88, None)
    query_volatile = _sig("query_volatile", "high", 610, 2.4, 2.3, None)
    all_sigs = calm_held + volatile_failed + [query_calm, query_volatile]
    result = pivot_confidence_series(all_sigs, k=8)
    assert result["query_calm"] == 1.0    # nearest 8 neighbors are all the calm/held group
    assert result["query_volatile"] == 0.0  # nearest 8 neighbors are all the volatile/failed group


def test_no_lookahead_pool_member_not_used_before_its_horizon_elapses():
    """The core correctness property: a candidate born at bar_index=100 with hold_horizon_bars=20
    only becomes usable evidence once a query's OWN bar_index >= 120 -- never for a query at
    bar_index=110, even though the candidate was iterated earlier and already has a `held` label
    in this bulk (non-point-in-time) computation."""
    early_candidate = _sig("early", "high", 100, 1.0, 1.0, True)
    # Pad with 7 OTHER already-resolved, sufficiently-old candidates so pool size could reach k=8
    # if (incorrectly) the early_candidate were also eligible.
    old_padding = [_sig(f"old{i}", "high", -500 + i * 10, 5.0, 5.0, False) for i in range(7)]
    query_too_soon = _sig("query_too_soon", "high", 110, 1.0, 1.0, None)  # only 10 bars after early_candidate -- horizon (20) not elapsed
    result = pivot_confidence_series(old_padding + [early_candidate, query_too_soon], k=8, hold_horizon_bars=20)
    # Only 7 padding candidates are eligible (early_candidate's horizon hasn't elapsed at bar 110) -> pool < k -> None
    assert result["query_too_soon"] is None

    query_late_enough = _sig("query_late_enough", "high", 121, 1.0, 1.0, None)  # 21 bars after -- horizon elapsed
    result2 = pivot_confidence_series(old_padding + [early_candidate, query_late_enough], k=8, hold_horizon_bars=20)
    assert result2["query_late_enough"] is not None  # now 8 eligible candidates (7 padding + early_candidate)


def _bars(values: list[tuple[float, float, float, float, float]]) -> list[dict]:
    start = _T0
    return [
        {"timestamp": (start + timedelta(minutes=15 * i)).isoformat(), "open": o, "high": h, "low": l, "close": c, "volume": v}
        for i, (o, h, l, c, v) in enumerate(values)
    ]


def _generated_series(n: int = 300, seed: int = 42) -> list[StructureBar]:
    rnd = random.Random(seed)
    price = 100.0
    rows = []
    for i in range(n):
        drift = rnd.uniform(-0.3, 0.3)
        price += drift
        o = price - rnd.uniform(0, 0.1)
        c = price + rnd.uniform(-0.1, 0.1)
        h = max(o, c) + rnd.uniform(0, 0.15)
        l = min(o, c) - rnd.uniform(0, 0.15)
        v = 100 + rnd.uniform(-30, 100)
        rows.append((o, h, l, c, v))
    return normalize_bars(_bars(rows), symbol="TEST", timeframe="M15")


def test_end_to_end_pipeline_smoke():
    bars = _generated_series()
    config = get_profile("balanced")
    swings = detect_swings(bars, config, symbol="TEST", timeframe="M15")
    assert swings
    signatures = compute_signatures(bars, swings)
    assert signatures
    for sig in signatures:
        assert sig.relative_atr > 0
        assert sig.relative_volume >= 0
    result = pivot_confidence_series(signatures)
    assert set(result.keys()) == {s.swing_id for s in signatures}
    populated = [v for v in result.values() if v is not None]
    assert all(0.0 <= v <= 1.0 for v in populated)
